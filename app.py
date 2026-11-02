"""Local chat GUI (Gradio) for the design-optimization agent.

Run from the repo root:  python app.py
Needs ANTHROPIC_API_KEY in the environment and a local ANSYS 2026 R1 install.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import gradio as gr

from src.agent.agent import MODE, MODEL, Agent
from src.agent.tools import Workbench

ROOT = Path(__file__).resolve().parent
wb = Workbench(ROOT / "outputs" / "session")  # closed in the __main__ block, not atexit: by then
# concurrent.futures has shut its pools down and wb.close fails
_agent: Agent | None = None

TABLE_HEADERS = ["design", "material", "mass (kg)", "max stress (MPa)", "safety factor", "max deflection (mm)",
                 "cost (USD)", "reaction balance error", "mesh check"]


def agent() -> Agent:
    global _agent
    if _agent is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise gr.Error("Set the ANTHROPIC_API_KEY environment variable and restart the app.")
        _agent = Agent(wb)
    return _agent


def _short(text: str, n: int = 1500) -> str:
    return text if len(text) <= n else text[:n] + " …"


def _stream(history: list, user_text: str):
    """Run one agent turn, yielding (chat history, design table) as events arrive."""
    for ev in agent().run(user_text):
        if ev["type"] == "text":
            history.append({"role": "assistant", "content": ev["text"]})
        elif ev["type"] == "tool":
            history.append({"role": "assistant", "content": f"```json\n{json.dumps(ev['input'], indent=1)}\n```",
                            "metadata": {"title": f"🔧 {ev['name']} (running…)", "status": "pending"}})
        elif ev["type"] == "progress":
            history[-1]["content"] += f"\n- {ev['text']}"
        elif ev["type"] == "tool_result":
            msg = history[-1]
            mark = "❌" if ev["is_error"] else "✅"
            msg["metadata"] = {"title": f"{mark} {ev['name']}", "status": "done"}
            msg["content"] += f"\n\nResult:\n```\n{_short(ev['content'])}\n```"
            if ev["is_error"] and "MeshTooLarge" in str(ev["content"]):
                text = str(ev["content"]).split("MeshTooLarge:", 1)[-1].strip()
                history.append({"role": "assistant", "content": f"⚠️ **Mesh over the Student limit, not solved.** {text}"})
        elif ev["type"] == "error":
            history.append({"role": "assistant", "content": f"⚠️ {ev['text']}"})
        yield history, wb.table()


# The chat log lives here, not in the browser: a turn that waits in the queue was sent with the
# browser's history from before the running turn finished, and would overwrite that turn's messages.
_chat: list[dict] = []


def on_upload(file, history):
    if file is None:
        return
    _chat[:] = [{"role": "assistant", "content": "Opening the part in SpaceClaim "
                 "(the first start takes about a minute)…"}]
    yield _chat, wb.table()
    wb.load_part(file)
    agent().reset()
    user_text = f"I uploaded my part '{Path(file).name}'. Please look at it and tell me what you see."
    _chat.append({"role": "user", "content": user_text})
    yield from _stream(_chat, user_text)


def on_send(user_text, history):
    if not user_text.strip():
        return
    _chat.append({"role": "user", "content": user_text})
    yield from _stream(_chat, user_text)


def take_text(user_text):
    """Empty the textbox at once (outside the queue) and hand the text on to on_send."""
    return "", user_text


def on_stop():
    wb.request_stop()


def on_open_ansys():
    if wb.final_part is None or not wb.final_part.exists():
        raise gr.Error("No final design yet. Ask the agent to finalize a design first.")
    os.startfile(wb.final_part)  # opens with the program registered for .scdocx (SpaceClaim)
    return gr.update(value=str(wb.final_part))


def mechanical_exe() -> Path | None:
    """Mechanical's own GUI program. Not the `ansys-mechanical` launcher: it fails until its install
    path is configured (seen 2026-11-01)."""
    root = os.environ.get("AWP_ROOT261", r"C:\Program Files\ANSYS Inc\ANSYS Student\v261")
    exe = Path(root) / "aisol" / "bin" / "winx64" / "AnsysWBU.exe"
    return exe if exe.exists() else None


def on_open_mechanical():
    """Open the finalized design's saved analysis in the Mechanical window."""
    mechdb = wb.final_analysis
    if mechdb is None or not mechdb.exists():
        raise gr.Error("No saved analysis yet. Ask the agent to finalize a design first.")
    exe = mechanical_exe()
    if exe:
        subprocess.Popen([str(exe), "-DSApplet", "-AppModeMech", "-file", str(mechdb)])
    else:
        os.startfile(mechdb)  # fall back to the program registered for .mechdb
    gr.Info("Opening ANSYS Mechanical; it takes about a minute to start.")
    return gr.update(value=str(mechdb))


with gr.Blocks(title="SimpleSim design optimizer") as demo:
    gr.Markdown("## SimpleSim: simulation-driven bracket optimization\n"
                "Upload your part, then tell the assistant how it is loaded and what you want to optimize.\n\n"
                f"Model: `{MODEL}` ({MODE} mode)")
    with gr.Row():
        with gr.Column(scale=3):
            chat = gr.Chatbot(height=600, label="Assistant")
            with gr.Row():
                box = gr.Textbox(placeholder="e.g. The back face is bolted to a wall, 500 N pushes down on the "
                                 "free end. Make it as light as possible with stress under 150 MPa.",
                                 show_label=False, scale=5)
                send = gr.Button("Send", variant="primary", scale=1)
            stop_btn = gr.Button("Stop search", variant="stop")
            pending = gr.State("")  # the message on its way from the textbox to on_send
        with gr.Column(scale=2):
            part = gr.File(label="Part file (.scdocx, .stp, .step)", file_types=[".scdocx", ".stp", ".step"],
                           type="filepath")
            table = gr.Dataframe(headers=TABLE_HEADERS, label="Evaluated designs", interactive=False)
            open_btn = gr.Button("Open final design in ANSYS")
            final_path = gr.Textbox(label="Final design file", interactive=False)
            mech_btn = gr.Button("Open analysis in Mechanical")
            analysis_path = gr.Textbox(label="Final analysis file", interactive=False)

    # One shared queue for every listener that drives the agent: a message sent while a turn is
    # running must wait, or two turns interleave in Agent.messages and the API rejects the history.
    one_turn = {"concurrency_id": "agent", "concurrency_limit": 1}
    part.upload(on_upload, [part, chat], [chat, table], **one_turn)
    send.click(take_text, box, [box, pending], queue=False).then(on_send, [pending, chat], [chat, table], **one_turn)
    box.submit(take_text, box, [box, pending], queue=False).then(on_send, [pending, chat], [chat, table], **one_turn)
    open_btn.click(on_open_ansys, None, final_path)
    mech_btn.click(on_open_mechanical, None, analysis_path)
    stop_btn.click(on_stop, None, None, queue=False)

if __name__ == "__main__":
    try:
        demo.launch(inbrowser=True)
    finally:
        wb.close()
