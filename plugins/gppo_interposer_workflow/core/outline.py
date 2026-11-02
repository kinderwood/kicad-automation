"""Validate intended outer/cutout topology before native serialization normalizes it.

Circular arcs are intersected analytically rather than approximated by chords.
Coordinates and tolerance are millimetres. Source ownership is retained by the
caller: this module never assumes ownership from group membership.
"""
import math
from dataclasses import dataclass

TAU = 2*math.pi

@dataclass(frozen=True)
class Segment:
    start: tuple
    end: tuple
    mid: tuple | None = None
    source_uuid: str = ''
    owner: str = ''

    def reversed(self):
        return Segment(self.end, self.start, self.mid, self.source_uuid, self.owner)


def _distance(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


def _cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1])


def _arc(segment, tolerance):
    a, b, c = segment.start, segment.mid, segment.end
    d = 2*_cross(_sub(b, a), _sub(c, a))
    if abs(d) <= tolerance*tolerance:
        raise ValueError('Arc points are collinear or degenerate')
    # Circumcenter in translated coordinates improves numerical conditioning.
    bx, by = _sub(b, a)
    cx, cy = _sub(c, a)
    center = (a[0]+(cy*(bx*bx+by*by)-by*(cx*cx+cy*cy))/d, a[1]+(bx*(cx*cx+cy*cy)-cx*(bx*bx+by*by))/d)
    radius = _distance(a, center)
    start, middle, end = (math.atan2(p[1]-center[1], p[0]-center[0]) for p in (a, b, c))
    ccw = (end-start) % TAU
    sweep = ccw if (middle-start) % TAU < ccw else ccw-TAU
    if abs(sweep)*radius <= tolerance:
        raise ValueError('Arc has zero length')
    return center, radius, start, sweep


def _on_arc(point, arc, tolerance):
    center, radius, start, sweep = arc
    if abs(_distance(point, center)-radius) > tolerance:
        return False
    angle = math.atan2(point[1]-center[1], point[0]-center[0])
    travel = (angle-start) % TAU if sweep > 0 else (start-angle) % TAU
    return travel <= abs(sweep)+tolerance/radius or TAU-travel <= tolerance/radius


def _on_line(point, segment, tolerance):
    a, b = segment.start, segment.end
    length = _distance(a, b)
    return abs(_cross(_sub(point, a), _sub(b, a))) <= tolerance*length and min(a[0], b[0])-tolerance <= point[0] <= max(a[0], b[0])+tolerance and min(a[1], b[1])-tolerance <= point[1] <= max(a[1], b[1])+tolerance


def _unique(points, tolerance):
    result = []
    for point in points:
        if not any(_distance(point, old) <= tolerance for old in result):
            result.append(point)
    return result


def _intersections(a, b, tolerance):
    """Return points and overlap flag (including coincident circular arcs)."""
    if a.mid is None and b.mid is None:
        p, q, r, s = a.start, b.start, _sub(a.end, a.start), _sub(b.end, b.start)
        denominator = _cross(r, s)
        if abs(denominator) <= tolerance*max(_distance(a.start, a.end), _distance(b.start, b.end)):
            points = _unique([v for v in (a.start, a.end, b.start, b.end) if _on_line(v, a, tolerance) and _on_line(v, b, tolerance)], tolerance)
            return points, len(points)>1
        t, u = _cross(_sub(q, p), s)/denominator, _cross(_sub(q, p), r)/denominator
        if -tolerance/_distance(a.start,a.end) <= t <= 1+tolerance/_distance(a.start,a.end) and -tolerance/_distance(b.start,b.end) <= u <= 1+tolerance/_distance(b.start,b.end):
            return [(p[0]+t*r[0],p[1]+t*r[1])], False
        return [], False
    if a.mid is None or b.mid is None:
        line, curved = (a,b) if a.mid is None else (b,a)
        arc = _arc(curved,tolerance)
        center, radius = arc[:2]
        vector, offset = _sub(line.end,line.start), _sub(line.start,center)
        aa, bb, cc = vector[0]**2+vector[1]**2, 2*(vector[0]*offset[0]+vector[1]*offset[1]), offset[0]**2+offset[1]**2-radius**2
        disc = bb*bb-4*aa*cc
        if disc < -tolerance*tolerance*aa:
            return [],False
        roots = [(-bb+sign*math.sqrt(max(0,disc)))/(2*aa) for sign in (-1,1)]
        points = [(line.start[0]+t*vector[0],line.start[1]+t*vector[1]) for t in roots if -tolerance/math.sqrt(aa) <= t <= 1+tolerance/math.sqrt(aa)]
        return _unique([p for p in points if _on_arc(p,arc,tolerance)], tolerance),False
    arc_a, arc_b = _arc(a,tolerance),_arc(b,tolerance)
    ca,ra = arc_a[:2]
    cb,rb = arc_b[:2]
    distance = _distance(ca,cb)
    if distance <= tolerance and abs(ra-rb) <= tolerance:
        points = _unique([p for p in (a.start,a.end,b.start,b.end) if _on_arc(p,arc_a,tolerance) and _on_arc(p,arc_b,tolerance)],tolerance)
        # Midpoint membership catches full containment as well as equal arcs.
        overlap = any(_on_arc(p,arc_a,tolerance) and _on_arc(p,arc_b,tolerance) and all(_distance(p,e)>tolerance for e in (a.start,a.end,b.start,b.end)) for p in (a.mid,b.mid))
        return points, overlap or len(points)>2
    if distance <= tolerance or distance > ra+rb+tolerance or distance < abs(ra-rb)-tolerance:
        return [],False
    along = (ra*ra-rb*rb+distance*distance)/(2*distance)
    height2 = ra*ra-along*along
    if height2 < -tolerance*max(ra,rb):
        return [],False
    height = math.sqrt(max(0,height2))
    vx,vy = (cb[0]-ca[0])/distance,(cb[1]-ca[1])/distance
    base = (ca[0]+along*vx,ca[1]+along*vy)
    points = [(base[0]+sign*height*(-vy),base[1]+sign*height*vx) for sign in (-1,1)]
    return _unique([p for p in points if _on_arc(p,arc_a,tolerance) and _on_arc(p,arc_b,tolerance)],tolerance),False


def _contour(segments,tolerance):
    remaining = [s if isinstance(s,Segment) else Segment(**s) for s in segments]
    if len(remaining)<2:
        raise ValueError('A contour requires at least two nondegenerate segments')
    for segment in remaining:
        points = (segment.start,segment.end)+((segment.mid,) if segment.mid is not None else ())
        if any(len(p)!=2 or not all(math.isfinite(float(v)) for v in p) for p in points):
            raise ValueError('Contour coordinates must be finite two-dimensional points')
        if _distance(segment.start,segment.end)<=tolerance:
            raise ValueError('Zero-length or full-circle segment is unsupported; split circles into arcs')
        if segment.mid is not None:
            _arc(segment,tolerance)
    result = [remaining.pop(0)]
    while remaining:
        end = result[-1].end
        matches = [(i,s,False) for i,s in enumerate(remaining) if _distance(end,s.start)<=tolerance]+[(i,s,True) for i,s in enumerate(remaining) if _distance(end,s.end)<=tolerance]
        if len(matches)!=1:
            raise ValueError('Contour is open, duplicated or ambiguously branched')
        index,segment,reverse = matches[0]
        result.append(segment.reversed() if reverse else segment)
        remaining.pop(index)
    if _distance(result[-1].end,result[0].start)>tolerance:
        raise ValueError('Contour is not closed')
    for i,a in enumerate(result):
        for j in range(i+1,len(result)):
            b = result[j]
            intersections,overlap = _intersections(a,b,tolerance)
            adjacent = j==i+1 or (i==0 and j==len(result)-1)
            shared = [p for p in (a.start,a.end) if any(_distance(p,q)<=tolerance for q in (b.start,b.end))]
            if overlap or (intersections and (not adjacent or any(not any(_distance(p,q)<=tolerance for q in shared) for p in intersections))):
                raise ValueError('Contour contains overlapping, duplicate or self-intersecting segments')
    return tuple(result)


def _inside(point,contour,tolerance):
    x,y = point
    crossings = 0
    for segment in contour:
        if segment.mid is None:
            a,b = segment.start,segment.end
            if (a[1]>y)!=(b[1]>y) and a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])>x:
                crossings += 1
            continue
        arc = _arc(segment,tolerance)
        center,radius,start,sweep = arc
        delta = y-center[1]
        if abs(delta)>=radius:
            continue # horizontal tangency does not cross
        dx = math.sqrt(max(0,radius*radius-delta*delta))
        for xx in (center[0]-dx,center[0]+dx):
            p = (xx,y)
            if xx<=x or not _on_arc(p,arc,tolerance):
                continue
            angle = math.atan2(delta,xx-center[0])
            derivative = math.cos(angle)*sweep
            at_start = _distance(p,segment.start)<=tolerance
            at_end = _distance(p,segment.end)<=tolerance
            if at_start and derivative<0 or at_end and derivative>0:
                continue
            crossings += 1
    return crossings%2==1


def validate_outline(outer, cutouts=(), tolerance=1e-6):
    """Return ordered contours or raise ValueError with a reviewable blocker.

    Unordered/reversed source segments are supported. Cutouts must be strictly
    contained, disjoint and non-nested; source UUID repetition blocks ownership
    ambiguity before any board object is copied.
    """
    if not math.isfinite(tolerance) or tolerance<=0:
        raise ValueError('Outline tolerance must be finite and positive')
    contours = [_contour(outer,tolerance)]+[_contour(c,tolerance) for c in cutouts]
    ids = [s.source_uuid for contour in contours for s in contour if s.source_uuid]
    if len(set(ids))!=len(ids):
        raise ValueError('Outline source UUID selected more than once')
    for i,a in enumerate(contours):
        for b in contours[i+1:]:
            if any(points or overlap for sa in a for sb in b for points,overlap in [_intersections(sa,sb,tolerance)]):
                raise ValueError('Outer/cutout contours touch, cross or overlap')
    for index,cutout in enumerate(contours[1:],1):
        if not _inside(cutout[0].start,contours[0],tolerance):
            raise ValueError('Cutout is outside the designated outer boundary')
        for other in contours[1:index]:
            if _inside(cutout[0].start,other,tolerance) or _inside(other[0].start,cutout,tolerance):
                raise ValueError('Nested cutouts are ambiguous')
    return tuple(contours)
