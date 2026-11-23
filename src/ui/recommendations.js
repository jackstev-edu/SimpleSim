// Render the study's existing finalists and shortlist. This view never searches for designs.
function recommendationSource(option, prediction) {
  return PROVENANCE[option.estimate ? predictionKind(prediction) : resultKind(option.source)].label;
}

function rankedRecommendations(result, prediction) {
  const { baseline, finalists, recommended, spec } = result;
  const geometry = (row) => JSON.stringify(spec.parameters.map(({ name }) => row.design[name]));
  const allEvaluated = [...finalists, ...(recommended ? [recommended] : [])];
  const failed = allEvaluated.filter((row) => !row.feasible);
  const excludedIds = new Set([baseline.design_id, ...failed.map((row) => row.design_id)]);
  const excludedGeometry = new Set([geometry(baseline), ...failed.map(geometry)]);
  const compare = ({ metric, direction }) => (a, b) =>
    (direction === "minimize" ? 1 : -1) * (recommendationValue(a, metric) - recommendationValue(b, metric));
  const seenIds = new Set(excludedIds), seenGeometry = new Set(excludedGeometry);
  const unique = (row) => {
    const shape = geometry(row);
    if (seenIds.has(row.design_id) || seenGeometry.has(shape)) return false;
    seenIds.add(row.design_id);
    seenGeometry.add(shape);
    return true;
  };

  const evaluated = allEvaluated.filter((row) => row.feasible).sort(compare(spec.objective)).filter(unique);
  const evaluatedIds = new Set(allEvaluated.map((row) => row.design_id));
  const estimates = prediction.shortlist.filter((row) => row.feasible_p95 && !evaluatedIds.has(row.design_id))
    .map((row) => ({ ...row, estimate: true })).sort(compare(spec.objective)).filter(unique);
  const top = evaluated.slice(0, 3);
  const more = [...evaluated.slice(3), ...estimates].slice(0, 2);
  return { top, more };
}

function recommendationValue(option, metric) {
  return option.estimate && metric !== "mass_kg" ? option[metric].value : option[metric];
}

function recommendationMargin(option, constraint) {
  const { metric, op, value } = constraint;
  const actual = option.estimate && metric !== "mass_kg"
    ? option[metric][op === ">=" ? "p05" : "p95"]
    : recommendationValue(option, metric);
  return op === ">=" ? actual - value : value - actual;
}

const recMetric = (metric, value) => metric === "max_disp_mm" ? `${sig(value, 4)} mm`
  : metric === "max_stress_mpa" ? `${sig(value, 4)} MPa` : metricText(metric, value);
const recMillimeters = (value) => `${sig(value, 8)} mm`;

function recommendationEdits(option, baseline, params) {
  return params.map(({ name }) => ({
    name, before: baseline.design[name], after: option.design[name], delta: option.design[name] - baseline.design[name],
  }));
}

function recommendationHeadline(edits) {
  const largest = edits.filter(({ delta }) => delta !== 0).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 2);
  if (!largest.length) return "Keep the baseline dimensions";
  return largest.map(({ name, after, delta }, i) => {
    const action = delta > 0 ? "Increase" : "Decrease";
    return `${i ? action.toLowerCase() : action} ${PARAM_LABEL[name].toLowerCase()} to ${sig(after, 4)} mm`;
  }).join(" and ");
}

function recommendationWhy(option, rank, best, spec) {
  if (option.estimate) return "The model estimates that this combination meets every requirement at the conservative end of its ranges.";
  const { metric, direction } = spec.objective;
  const name = SHORT_LABEL[metric].toLowerCase();
  const delta = Math.abs(recommendationValue(option, metric) - recommendationValue(best, metric));
  if (rank === 1) {
    const goal = direction === "minimize" ? "lowest" : "highest";
    return `This combination meets the study requirements and has the ${goal} ${name} among the feasible finalists shown.`;
  }
  const relative = direction === "minimize" ? "higher" : "lower";
  const why = delta === 0 ? `Tied with #1 on ${name}. Backend order breaks the tie.`
    : `${recMetric(metric, delta)} ${relative} ${name} than #1. Both meet the study requirements.`;
  if (metric !== "safety_factor" && option.safety_factor > best.safety_factor) return `${why} This option has a higher factor of safety.`;
  if (metric !== "max_disp_mm" && option.max_disp_mm < best.max_disp_mm) return `${why} This option has less displacement.`;
  return why;
}

function recommendationPerformance(option, baseline) {
  const mass = option.mass_kg, reduction = 100 * (baseline.mass_kg - mass) / baseline.mass_kg;
  const change = Number.isFinite(reduction) && reduction !== 0
    ? ` (${sig(Math.abs(reduction), 3)}% ${reduction > 0 ? "lighter" : "heavier"})` : "";
  return `<dl class="rec-performance">
    <div><dt>Mass</dt><dd>${recMetric("mass_kg", mass)}${change}</dd></div>
    <div><dt>Factor of safety</dt><dd>${recMetric("safety_factor", recommendationValue(option, "safety_factor"))}</dd></div>
    <div><dt>Displacement</dt><dd>${recMetric("max_disp_mm", recommendationValue(option, "max_disp_mm"))}</dd></div>
  </dl>`;
}

function recommendationConstraints(option, result) {
  const constraints = result.spec.constraints;
  const margins = constraints.map((constraint) => recommendationMargin(option, constraint));
  const closest = constraints.reduce((best, constraint, i) => {
    const scale = (c) => Math.abs(c.value) || 1;
    return best < 0 || margins[i] / scale(constraint) < margins[best] / scale(constraints[best]) ? i : best;
  }, -1);
  const exact = option.design_id === result.recommended?.design_id ? result.binding_constraint : null;
  const match = exact?.match(/^(\S+) (>=|<=) (\S+)$/);
  const reported = match ? constraints.findIndex((c) => c.metric === match[1] && c.op === match[2] && c.value === Number(match[3])) : -1;
  const rows = constraints.map(({ metric, op, value }, i) => {
    const label = i === reported ? " · binding limit" : i === closest && reported < 0 ? " · closest limit" : "";
    const margin = margins[i];
    return `<li>${SHORT_LABEL[metric]} ${opSym(op)} ${recMetric(metric, value)} ·
      ${margin < 0 ? "shortfall" : "margin"} ${recMetric(metric, Math.abs(margin))}${label}</li>`;
  }).join("");
  return `<ul class="rec-limits" aria-label="${option.estimate ? "Conservative constraint margins" : "Constraint margins"}">${rows}</ul>`;
}

function recommendationDetails(option, edits, prediction, result = null) {
  const source = option.estimate ? prediction.model_name : option.source;
  const band = option.estimate ? `<p class="mutedp">Model ranges (p05–p95). Margins use the conservative end of each range.</p>
    <ul>${["safety_factor", "max_disp_mm", "max_stress_mpa"].map((metric) =>
      `<li>${SHORT_LABEL[metric]}: ${recMetric(metric, option[metric].p05)} to ${recMetric(metric, option[metric].p95)}</li>`).join("")}</ul>` : "";
  return `<details class="rec-dimensions">
    <summary>Inspect dimensions</summary>
    ${result ? recommendationConstraints(option, result) : ""}
    <table class="rt"><thead><tr><th scope="col">Dimension</th><th scope="col">Before</th><th scope="col">After</th><th scope="col">Change</th></tr></thead>
      <tbody>${edits.map(({ name, before, after, delta }) => `<tr><th scope="row">${esc(PARAM_LABEL[name])}</th>
        <td>${recMillimeters(before)}</td><td>${recMillimeters(after)}</td><td>${delta > 0 ? "+" : ""}${recMillimeters(delta)}</td></tr>`).join("")}</tbody>
    </table>
    <p class="mutedp">Source ${esc(source)}.${option.estimate && option.in_training_range === false ? " Outside the training range." : ""}</p>${band}
  </details>`;
}

function recommendationCard(option, rank, best, result, prediction) {
  const edits = recommendationEdits(option, result.baseline, result.spec.parameters);
  return `<details class="rec-card" data-rank="${rank}"${rank === 1 ? " open" : ""}>
    <summary><strong>#${rank} ${rank === 1 ? "Recommended first" : esc(recommendationHeadline(edits))}</strong>
      <span class="rec-source">${recommendationSource(option, prediction)}</span></summary>
    ${rank === 1 ? `<p class="rec-action">${esc(recommendationHeadline(edits))}.</p>` : ""}
    <p>${esc(recommendationWhy(option, rank, best, result.spec))}</p>
    ${recommendationPerformance(option, result.baseline)}
    ${recommendationConstraints(option, result)}
    ${recommendationDetails(option, edits, prediction)}
  </details>`;
}

function recommendationOptions(result, prediction) {
  const { top, more } = rankedRecommendations(result, prediction);
  const objective = result.spec.objective;
  return `<p class="rec-objective">Goal: ${objective.direction} ${SHORT_LABEL[objective.metric].toLowerCase()}</p>
    ${top.length ? top.map((option, i) => recommendationCard(option, i + 1, top[0], result, prediction)).join("")
      : '<p class="empty">No evaluated design met every requirement.</p>'}
    ${more.length ? `<details class="rec-more"><summary>Other feasible options (${more.length})</summary>
      ${more.map((option, i) => `<section class="rec-compact" data-rank="${top.length + i + 1}">
        <strong>#${top.length + i + 1} ${option.estimate ? "Predicted feasible" : "Meets requirements"}</strong>
        <span class="rec-source">${recommendationSource(option, prediction)}</span>
        ${recommendationPerformance(option, result.baseline)}
        ${recommendationDetails(option, recommendationEdits(option, result.baseline, result.spec.parameters), prediction, result)}
      </section>`).join("")}</details>` : ""}`;
}

function isRecommendationFixture(result, prediction) {
  return result.spec.bracket_family === "l_bracket_v0" && prediction.model_name === "fake_formula"
    && [result.baseline, ...result.finalists, result.recommended].filter(Boolean).every(({ source }) => source === "fake_formula");
}

function recommendationStudyNotes(result, prediction) {
  const warnings = provenanceWarnings(prediction, result);
  return `<details class="rec-study"><summary>Study explanation and model notes${warnings.length ? ` (${warnings.length} warnings)` : ""}</summary>
    <p>${esc(resultExplanation(result))}</p>
    <p class="mutedp">${esc(predictionSummary(prediction))}</p>
    ${warnings.length ? `<ul class="rec-limits">${warnings.map((warning) => `<li>${esc(warning)}</li>`).join("")}</ul>` : ""}
  </details>`;
}
