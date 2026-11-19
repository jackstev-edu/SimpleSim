"""Six additional bracket families. All coordinates use the shared v0 frame."""
from math import pi, sqrt

EXTRA = {
    'T05': {'hole_diameter_mm': (6., 10.), 'notch_radius_mm': (3., 6.)},
    'T06': {'hole_diameter_mm': (6., 10.), 'tip_trim_mm': (3., 6.)},
    'T07': {'hole_diameter_mm': (6., 10.), 'step_trim_mm': (3., 6.)},
    'T08': {'hole_diameter_mm': (6., 10.), 'gusset_thickness_mm': (3., 5.), 'gusset_length_mm': (16., 28.)},
    'T09': {'hole_diameter_mm': (6., 10.), 'gusset_thickness_mm': (5., 9.), 'gusset_length_mm': (28., 35.), 'rib_hole_diameter_mm': (5., 8.)},
    'T10': {'hole_diameter_mm': (6., 10.), 'offset_trim_mm': (6., 12.)},
}
NAMES = {'T05':'side_notches','T06':'tapered','T07':'stepped','T08':'twin_gusset','T09':'perforated_gusset','T10':'offset_planform'}
PRIMARY = {'T05':'notch_radius_mm','T06':'tip_trim_mm','T07':'step_trim_mm','T08':'gusset_thickness_mm','T09':'rib_hole_diameter_mm','T10':'offset_trim_mm'}
# Keep all baselines centered in their ranges.
BASE_PARAMS = {k:{p:(lo+hi)/2 for p,(lo,hi) in d.items()} for k,d in EXTRA.items()}


def _prism(cq,points,t):
    return cq.Workplane('XZ',origin=(0,t+.1,0)).polyline(points).close().extrude(t+.2).val()


def _smooth_taper_cut(cq,x,L,W,a,t,upper=False):
    # Cubic Bezier joins both straight boundaries tangentially; no near-collinear fillet.
    z=lambda v: W-v if upper else v
    end=.94*L; span=end-x
    v=lambda X,Z: cq.Vector(X,-.1,z(Z))
    curve=cq.Edge.makeBezier([v(x,0),v(x+span/3,0),v(end-span/3,a),v(end,a)])
    pts=[v(end,a),v(L+2,a),v(L+2,-2),v(x,-2),v(x,0)]
    wire=cq.Wire.assembleEdges([curve,*[cq.Edge.makeLine(p,q) for p,q in zip(pts,pts[1:])]])
    return cq.Solid.extrudeLinear(wire,[],cq.Vector(0,t+.2,0))


def _hole(cq,x,z,t,r):
    return cq.Solid.makeCylinder(r,t+.2,cq.Vector(x,-.1,z),cq.Vector(0,1,0))


def _round_vertical_edges(solid, targets, radius):
    edges=[]
    for x,z in targets:
        matches=[]
        for e in solid.Edges():
            b=e.BoundingBox()
            if abs(b.xmin-x)<1e-6 and abs(b.xmax-x)<1e-6 and abs(b.zmin-z)<1e-6 and abs(b.zmax-z)<1e-6 and b.ylen>1e-6:
                matches.append(e)
        if len(matches)!=1:
            raise ValueError(f'Expected one vertical shoulder edge at {x},{z}; found {len(matches)}')
        edges.extend(matches)
    return solid.fillet(radius,edges).clean()


def tip_width(case):
    p=case.params; w=p['arm_width_mm']; key=case.template_id
    if key=='T06': return w-2*p['tip_trim_mm']
    if key=='T07': return w-2*p['step_trim_mm']
    if key=='T10': return w-p['offset_trim_mm']
    return w


def _gusset(cq, t, g, z0, gt):
    # Preserve the 45 degree hypotenuse while overlapping both legs by 0.5 mm.
    return cq.Workplane('XY',origin=(0,0,z0)).polyline([(t-.5,t-.5),(t+g+.5,t-.5),(t-.5,t+g+.5)]).close().extrude(gt).val()


def _add_ribs(cq,solid,case):
    p=case.params; t=p['thickness_mm']; w=p['arm_width_mm']; g=p['gusset_length_mm']; gt=p['gusset_thickness_mm']
    centres=(.27*w,.73*w) if case.template_id=='T08' else (.5*w,)
    if g<p['fillet_radius_mm']+4 or t+g+2>p['vertical_height_mm']-3: raise ValueError('Invalid gusset reach')
    if min(c-gt/2-2 for c in centres)<3 or min(w-c-gt/2-2 for c in centres)<3: raise ValueError('Insufficient rib side clearance')
    if len(centres)>1 and centres[1]-centres[0]-gt-4<3: raise ValueError('Insufficient rib-to-rib clearance')
    fused=solid.fuse(*[_gusset(cq,t,g,c-gt/2,gt) for c in centres]).clean()
    def rib_face(f):
        c=f.Center()
        return any(abs(c.z-z)<=gt/2+1e-6 for z in centres) and c.x>t+1e-6 and c.y>t+1e-6
    rib_faces=[f for f in fused.Faces() if rib_face(f)]
    other_edges=[e for f in fused.Faces() if not rib_face(f) for e in f.Edges()]
    junction=[]
    for f in rib_faces:
        for e in f.Edges():
            if any(e.isSame(o) for o in other_edges) and not any(e.isSame(o) for o in junction): junction.append(e)
    if len(junction)!=8*len(centres): raise ValueError(f'Expected {8*len(centres)} rib junction edges, got {len(junction)}')
    rounded=fused.fillet(2.,junction).clean()
    if case.template_id=='T09':
        rh=p['rib_hole_diameter_mm']/2; x=y=t+.30*g
        clearance=min(.30*g-2.,.40*g/sqrt(2))-rh
        if clearance<2: raise ValueError('Rib hole clearance below 2 mm')
        # The cylinder is separated from both plates and spans the rib thickness.
        cutter=cq.Solid.makeCylinder(rh,gt+.2,cq.Vector(x,y,centres[0]-gt/2-.1),cq.Vector(0,0,1))
        before=rounded.Volume(); rounded=rounded.cut(cutter).clean()
        if abs((before-rounded.Volume())-pi*rh**2*gt)>1e-5*before: raise ValueError('Rib opening volume mismatch')
    return rounded


def build_additional(cq,solid,case):
    p=case.params; key=case.template_id; t=p['thickness_mm']; L=p['flange_length_mm']; W=p['arm_width_mm']; r=p['fillet_radius_mm']
    d=p['hole_diameter_mm']; hx=.76*L; hz=.5*W
    if key=='T05':
        n=p['notch_radius_mm']; x=.47*L
        if x-n<t+r+3 or hx-d/2-x-n<3 or W-2*n<3: raise ValueError('Notch clearance')
        before=solid.Volume(); solid=solid.cut(_hole(cq,x,0,t,n),_hole(cq,x,W,t,n)).clean()
        if abs(before-solid.Volume()-pi*n*n*t)>1e-6*before: raise ValueError('Notch volume mismatch')
        # Smooth the two re-entrant junctions at each notch mouth.
        solid=_round_vertical_edges(solid,[(x-n,0),(x+n,0),(x-n,W),(x+n,W)],.8)
    elif key in ('T06','T07','T10'):
        x=.50*L
        if key=='T06':
            a=p['tip_trim_mm']
            solid=solid.cut(_smooth_taper_cut(cq,x,L,W,a,t),_smooth_taper_cut(cq,x,L,W,a,t,upper=True)).clean()
        else:
            a=p['step_trim_mm'] if key=='T07' else p['offset_trim_mm']
            cutters=[_prism(cq,[(x,-2),(L+2,-2),(L+2,a),(x,a)],t)]
            targets=[(x,0),(x,a)]
            if key=='T07':
                cutters.append(_prism(cq,[(x,W-a),(L+2,W-a),(L+2,W+2),(x,W+2)],t)); targets.extend([(x,W),(x,W-a)])
            else: hz=(W+a)/2
            solid=solid.cut(*cutters).clean()
            solid=_round_vertical_edges(solid,targets,1.)
        if hx-d/2<x+3 or tip_width(case)/2-d/2<3: raise ValueError('Tip hole clearance')
    elif key in ('T08','T09'):
        if hx-d/2-(t+p['gusset_length_mm']+2)<3: raise ValueError('Rib to flange hole clearance')
        solid=_add_ribs(cq,solid,case)
    else: raise ValueError(key)
    if hx-d/2<t+r+3 or L-hx-d/2<3 or tip_width(case)/2-d/2<3: raise ValueError('Flange hole clearance')
    before=solid.Volume(); result=solid.cut(_hole(cq,hx,hz,t,d/2)).clean()
    if abs(before-result.Volume()-pi*(d/2)**2*t)>1e-6*before: raise ValueError('Flange hole volume mismatch')
    return result
