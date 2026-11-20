"""Common CAD-only features in the original support/load coordinate frame."""
from math import isfinite
from .templates import planar_x_faces

FEATURE_NAMES = ('bbox_x_mm','bbox_y_mm','bbox_z_mm','plate_thickness_mm','poissons_ratio',
    'cad_volume_mm3','surface_area_mm2','center_x_mm','center_y_mm','center_z_mm',
    'inertia_xx_mm5','inertia_yy_mm5','inertia_zz_mm5','inertia_xy_mm5','inertia_xz_mm5','inertia_yz_mm5',
    'fixed_face_area_mm2','load_face_area_mm2','load_center_y_mm','load_center_z_mm')


def extract_features(case, solid):
    if not solid.isValid() or len(solid.Solids()) != 1:
        raise ValueError('Expected a single valid solid')
    b = solid.BoundingBox(); c = solid.Center(); volume = float(solid.Volume())
    if volume <= 0: raise ValueError('Nonpositive CAD volume')
    fixed = planar_x_faces(solid, 0.); load = planar_x_faces(solid, case.flange_length_mm)
    if len(fixed) != 1 or len(load) != 1: raise ValueError('Fixed/load faces must be unique')
    m = solid.matrixOfInertia(solid); lc = load[0].Center()
    raw = (b.xlen,b.ylen,b.zlen,case.thickness_mm,case.material.poissons_ratio,volume,solid.Area(),c.x,c.y,c.z,
           m[0][0],m[1][1],m[2][2],m[0][1],m[0][2],m[1][2],fixed[0].Area(),load[0].Area(),lc.y,lc.z)
    out = dict(zip(FEATURE_NAMES,map(float,raw),strict=True))
    if not all(isfinite(v) for v in out.values()): raise ValueError('Nonfinite CAD feature')
    return out
