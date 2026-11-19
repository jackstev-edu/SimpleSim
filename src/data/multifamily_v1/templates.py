"""Parameterized L-bracket variants, geometry gate. All dimensions in mm."""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite, pi
from scipy.stats import qmc
from ..bracket_spec import MaterialProperties
from ..family import GeometryFamily
from ..geometry import _cadquery, _find_inside_corner_edge

COMMON = {"thickness_mm": (4.,10.), "flange_length_mm": (75.,120.), "arm_width_mm": (32.,60.), "fillet_radius_mm": (2.,8.), "vertical_height_mm": (50.,90.)}
COMMON_BASE = dict(zip(COMMON, (6.,80.,40.,4.,60.)))
EXTRA = {
    "T01": {"hole_diameter_mm": (6.,10.)},
    "T02": {"slot_width_mm": (6.,10.), "slot_straight_length_mm": (8.,18.)},
    "T03": {"slot_width_mm": (6.,10.), "slot_straight_length_mm": (8.,18.)},
    "T04": {"window_length_mm": (18.,30.), "window_width_mm": (12.,20.), "window_radius_mm": (2.,4.)},
}
NAMES = {"T01":"double_hole", "T02":"single_slot", "T03":"twin_slot", "T04":"rounded_window"}
PRIMARY = {"T01":"hole_diameter_mm", "T02":"slot_width_mm", "T03":"slot_width_mm", "T04":"window_width_mm"}
BASE_PARAMS = {"T01":{"hole_diameter_mm":8.}, "T02":{"slot_width_mm":8.,"slot_straight_length_mm":13.}, "T03":{"slot_width_mm":8.,"slot_straight_length_mm":13.}, "T04":{"window_length_mm":24.,"window_width_mm":16.,"window_radius_mm":3.}}
from . import additional_templates as added
EXTRA.update(added.EXTRA)
NAMES.update(added.NAMES)
PRIMARY.update(added.PRIMARY)
BASE_PARAMS.update(added.BASE_PARAMS)
SEED = 20261117

@dataclass(frozen=True)
class TemplateCase:
    design_id: str
    template_id: str
    params: dict[str,float]
    material: MaterialProperties
    load_force_n: float = 500.
    sampling_method: str = "baseline"
    anchor_id: str = ""
    changed_parameter: str = ""
    @property
    def load_vector_n(self):
        return (0.,-self.load_force_n,0.)
    @property
    def flange_length_mm(self):
        return self.params["flange_length_mm"]
    @property
    def thickness_mm(self):
        return self.params["thickness_mm"]


def ranges(template_id):
    common = dict(COMMON)
    if template_id in ("T08", "T09"):
        common["fillet_radius_mm"] = (2.5, 8.)
    return {**common,**EXTRA[template_id],"poissons_ratio":(.25,.35)}


def make_case(template_id, design_id, params, *, sampling_method="baseline", anchor_id="", changed_parameter=""):
    limits=ranges(template_id)
    if set(params)!=set(limits):
        raise ValueError(f"Parameter keys differ: {set(params)^set(limits)}")
    params={k:float(params[k]) for k in limits}
    for k,(lo,hi) in limits.items():
        if not isfinite(params[k]) or not lo<=params[k]<=hi:
            raise ValueError(f"{k} outside [{lo}, {hi}]")
    mat=MaterialProperties(material_key="role1_isotropic_reference",youngs_modulus_mpa=200000.,poissons_ratio=params.pop("poissons_ratio"),yield_strength_mpa=250.,density_kg_m3=7850.)
    return TemplateCase(design_id,template_id,params,mat,500.,sampling_method,anchor_id,changed_parameter)


def baseline(template_id):
    return make_case(template_id,f"{template_id}_baseline",{**COMMON_BASE,**BASE_PARAMS[template_id],"poissons_ratio":.30})


def sample_plan(template_id):
    """60 LHS and 20 single-parameter variants of ten anchors."""
    lim=ranges(template_id); keys=tuple(lim); lo,hi=zip(*lim.values())
    rows=qmc.scale(qmc.LatinHypercube(d=len(keys),seed=SEED+int(template_id[1:])).random(n=60),lo,hi)
    cases=[make_case(template_id,f"{template_id}_lhs_{i:03d}",dict(zip(keys,row)),sampling_method="lhs",anchor_id=f"{template_id}_lhs_{i:03d}") for i,row in enumerate(rows)]
    for a in cases[:10]:
        p={**a.params,"poissons_ratio":a.material.poissons_ratio}
        for key,target,suffix in [("thickness_mm",lim["thickness_mm"][0],"thin"),(PRIMARY[template_id],lim[PRIMARY[template_id]][0 if template_id == "T08" else 1],"cut")]:
            new=dict(p); new[key]=(p[key]+target)/2.
            cases.append(make_case(template_id,f"{a.design_id}_{suffix}",new,sampling_method="paired",anchor_id=a.design_id,changed_parameter=key))
    return cases


def _box(cq,dx,dy,dz,x,y,z):
    return cq.Solid.makeBox(dx,dy,dz,cq.Vector(x,y,z))


def _cylinder(cq,r,t,x,z):
    return cq.Solid.makeCylinder(r,t+.2,cq.Vector(x,-.1,z),cq.Vector(0,1,0))


def _slot(cq,t,x,z,w,s):
    r=w/2.; left,right=x-s/2.,x+s/2.
    return _box(cq,s,t+.2,w,left,-.1,z-r).fuse(_cylinder(cq,r,t,left,z),_cylinder(cq,r,t,right,z)).clean()


def _window(cq,t,x,z,l,w,r):
    x0,z0=x-l/2.,z-w/2.
    a=_box(cq,l-2*r,t+.2,w,x0+r,-.1,z0)
    b=_box(cq,l,t+.2,w-2*r,x0,-.1,z0+r)
    corners=[_cylinder(cq,r,t,cx,cz) for cx in (x0+r,x0+l-r) for cz in (z0+r,z0+w-r)]
    return a.fuse(b,*corners).clean()


def opening_area_mm2(case):
    """Area of closed through-openings in the horizontal plate, excluding edge trims and rib holes."""
    p=case.params
    if case.template_id in added.EXTRA: return pi*(p["hole_diameter_mm"]/2)**2
    if case.template_id=="T01": return 2*pi*(p["hole_diameter_mm"]/2)**2
    if case.template_id in ("T02","T03"):
        w,s=p["slot_width_mm"],p["slot_straight_length_mm"]
        return (2 if case.template_id=="T03" else 1)*(w*s+pi*(w/2)**2)
    l,w,r=p["window_length_mm"],p["window_width_mm"],p["window_radius_mm"]
    return l*w-(4-pi)*r*r


def build_template(case):
    cq=_cadquery(); p=case.params
    t,L,W,r,H=(p[k] for k in COMMON)
    sharp=_box(cq,L,t,W,0,0,0).fuse(_box(cq,t,H,W,0,0,0)).clean()
    solid=sharp.fillet(r,[_find_inside_corner_edge(sharp,thickness_mm=t,width_mm=W)])
    if case.template_id in added.EXTRA:
        result=added.build_additional(cq,solid,case)
        if not result.isValid() or len(result.Solids())!=1 or result.Volume()<=0:
            raise ValueError(f"{case.design_id}: invalid or disconnected solid")
        return result
    if case.template_id=="T01":
        cutters=[_cylinder(cq,p["hole_diameter_mm"]/2,t,L*f,W/2) for f in (.50,.78)]
    elif case.template_id in ("T02","T03"):
        centers=(.5,) if case.template_id=="T02" else (.28,.72)
        cutters=[_slot(cq,t,.65*L,W*f,p["slot_width_mm"],p["slot_straight_length_mm"]) for f in centers]
    elif case.template_id=="T04":
        cutters=[_window(cq,t,.65*L,.5*W,p["window_length_mm"],p["window_width_mm"],p["window_radius_mm"])]
    else: raise ValueError(case.template_id)
    for cutter in cutters:
        b=cutter.BoundingBox()
        if min(b.xmin-(t+r),L-b.xmax,b.zmin,W-b.zmax)<3.-1e-6:
            raise ValueError(f"{case.design_id}: opening clearance under 3 mm")
    if len(cutters)==2:
        a,b=[c.BoundingBox() for c in cutters]
        if max(b.xmin-a.xmax,a.xmin-b.xmax,b.zmin-a.zmax,a.zmin-b.zmax)<3.-1e-6:
            raise ValueError(f"{case.design_id}: opening-to-opening clearance under 3 mm")
    result=solid.cut(*cutters).clean()
    if not result.isValid() or len(result.Solids())!=1 or result.Volume()<=0:
        raise ValueError(f"{case.design_id}: invalid or disconnected solid")
    expected=solid.Volume()-t*opening_area_mm2(case)
    if abs(result.Volume()-expected)>1e-6*expected:
        raise ValueError(f"{case.design_id}: opening volume mismatch")
    return result


def planar_x_faces(solid,x):
    return [f for f in solid.Faces() if f.geomType()=="PLANE" and abs(f.BoundingBox().xmin-x)<1e-5 and abs(f.BoundingBox().xmax-x)<1e-5]


def check_faces(case,solid):
    fixed=planar_x_faces(solid,0.); load=planar_x_faces(solid,case.flange_length_mm); p=case.params
    if len(fixed)!=1 or len(load)!=1: raise ValueError(f"{case.design_id}: ambiguous fixed/load faces")
    for face,area in [(fixed[0],p["vertical_height_mm"]*p["arm_width_mm"]),(load[0],p["thickness_mm"]*(added.tip_width(case) if case.template_id in added.EXTRA else p["arm_width_mm"]))]:
        if abs(face.Area()-area)>1e-6*area: raise ValueError(f"{case.design_id}: support/load face area mismatch")
    return fixed[0].Area(),load[0].Area()

FAMILIES={k:GeometryFamily(key=f"{k.lower()}_{n}_v1",build=build_template,load_face_x_mm=lambda c:c.flange_length_mm) for k,n in NAMES.items()}
