## from __future__ import (absolute_import, division,
##         print_function, unicode_literals)
#from builtins import *

import sys
import os
import re
import math
import json
freecad_name = None
if os.path.exists("/usr/lib/freecad-daily"):
    freecad_name = "freecad-daily"
elif os.path.exists("/usr/lib/freecad"):
    freecad_name = "freecad"

sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, "/usr/lib/freecad-daily-python3/lib/")
sys.path.insert(0, "/home/asepahvand/.local/share/FreeCAD/Macro/")

if freecad_name is not None:
    sys.path.append(f"/usr/lib/{freecad_name}/lib")
    sys.path.append(f"/usr/share/{freecad_name}/Ext")
    sys.path.append(f"/usr/share/{freecad_name}/Mod/BIM")
    sys.path.append(f"/usr/share/{freecad_name}/Mod")
    sys.path.append(f"/usr/share/{freecad_name}/Mod/Part")
    sys.path.append(f"/usr/share/{freecad_name}/Mod/Draft")
    sys.path.append(f"/usr/share/{freecad_name}/Mod/CAM")
    sys.path.append(f"/usr/share/{freecad_name}/Mod/Draft/draftobjects")
    # Comment line 31 from /usr/share/freecad-daily/Mod/Draft/draftutils/params.py if it crashes at import
else:
    sys.path.append("/usr/local/lib")
    sys.path.append("/usr/local/Ext")
    sys.path.append("/usr/local/Mod")
    sys.path.append("/usr/local/Mod/Part")
    sys.path.append("/usr/local/Mod/Draft")
    sys.path.append("/usr/local/Mod/Draft")
    sys.path.append("/usr/local/Mod/Draft/draftobjects")

from collections import defaultdict
from math import sqrt, atan2, degrees, sin, cos, radians, pi, hypot
import traceback
import json
import FreeCAD
import FreeCADGui
import Part

from FreeCAD import Console,Vector,Placement,Rotation
import DraftGeomUtils,DraftVecUtils
import Path
import CAM
import sys, os
import re
sys.path.append(os.path.dirname(os.path.realpath(__file__)))
from .kicad_parser import KicadPCB,SexpList,SexpParser,parseSexp
from .kicad_parser import unquote

PY3 = sys.version_info[0] == 3
if PY3:
    string_types = str,
else:
    string_types = basestring,

_hasElementMapping = hasattr(Part, 'disableElementMapping')

def disableTopoNaming(obj, enable=True):
    if _hasElementMapping:
        if isinstance(obj, FreeCAD.DocumentObject):
            Part.disableElementMapping(obj, enable)
        else:
            obj.Tag = -1 if enable else 0
    return obj

def addObject(doc, tp, name):
    obj = doc.addObject(tp, name)
    disableTopoNaming(obj)
    try:
        obj.ValidateShape = False
        obj.FixShape = 0
    except Exception:
        pass
    return obj

def setObjectLinks(obj, links, objs):
    if not _hasElementMapping or not objs:
        setattr(obj,links,objs)
        return

    if not isinstance(objs, (list,tuple)):
        objlist = [objs,]
    else:
        objlist = objs

    color = None
    if isinstance(objlist[0], FreeCAD.DocumentObject):
        color = objlist[0].ViewObject.DiffuseColor
        for o in objlist[1:]:
            if o.ViewObject.DiffuseColor != color:
                for o in objlist:
                    # re-enable topo naming to combine colors
                    disableTopoNaming(o, False)
                disableTopoNaming(obj, False)
                colors = None
                break
    setattr(obj,links,objs)
    if color:
        obj.ViewObject.DiffuseColor = color

def updateGui():
    try:
        FreeCADGui.updateGui()
    except Exception:
        pass

class FCADLogger:
    def __init__(self, tag):
        self.tag = tag
        self.levels = { 'error':0, 'warning':1, 'info':2,
                'log':3, 'trace':4 }

    def _isEnabledFor(self,level):
        return FreeCAD.getLogLevel(self.tag) >= level

    def isEnabledFor(self,level):
        return self._isEnabledFor(self.levels[level])

    def trace(self,msg):
        if self._isEnabledFor(4):
            FreeCAD.Console.PrintLog(msg+'\n')
            updateGui()

    def log(self,msg):
        if self._isEnabledFor(3):
            FreeCAD.Console.PrintLog(msg+'\n')
            updateGui()

    def info(self,msg):
        if self._isEnabledFor(2):
            FreeCAD.Console.PrintMessage(msg+'\n')
            updateGui()

    def warning(self,msg):
        if self._isEnabledFor(1):
            FreeCAD.Console.PrintWarning(msg+'\n')
            updateGui()

    def error(self,msg):
        if self._isEnabledFor(0):
            FreeCAD.Console.PrintError(msg+'\n')
            updateGui()

logger = FCADLogger('fcad_pcb')

def getActiveDoc():
    if FreeCAD.ActiveDocument is None:
        return FreeCAD.newDocument('kicad_fcad')
    return FreeCAD.ActiveDocument

def fitView():
    try:
        FreeCADGui.ActiveDocument.ActiveView.fitAll()
    except Exception:
        pass

def isZero(f):
    return round(f,DraftGeomUtils.precision())==0

def makeColor(*color):
    if len(color)==1:
        if isinstance(color[0],string_types):
            color = int(color[0],0)
        else:
            color = color[0]
        r = float((color>>24)&0xFF)
        g = float((color>>16)&0xFF)
        b = float((color>>8)&0xFF)
    else:
        r,g,b = color
    return (r/255.0,g/255.0,b/255.0)

def makeVect(l):
    return Vector(l[0],-l[1],0)

def getAt(sexp):
    at = getattr(sexp, 'at', None)
    if not at:
        return Vector(0,0,0),0
    v = makeVect(at)
    return (v,0) if len(at)==2 else (v,at[2])

def product(v1,v2):
    return Vector(v1.x*v2.x,v1.y*v2.y,v1.z*v2.z)

def make_rect(size,params=None):
    _ = params
    return Part.makePolygon([product(size,Vector(*v))
        for v in ((-0.5,-0.5),(0.5,-0.5),(0.5,0.5),(-0.5,0.5),(-0.5,-0.5))])

def make_trapezoid(size,params):
    pts = [product(size,Vector(*v)) \
            for v in ((-0.5,0.5),(-0.5,-0.5),(0.5,-0.5),(0.5,0.5))]
    try:
        delta = params.rect_delta[0]
        if delta:
            # horizontal
            idx = 1
            length = size[1]
        else:
            # vertical
            delta = params.rect_delta[1]
            idx = 0
            length = size[0]
        if delta <= -length:
            collapse = 1
            delta = -length;
        elif delta >= length:
            collapse = -1
            delta = length
        else:
            collapse = 0
        pts[0][idx] += delta*0.5
        pts[1][idx] -= delta*0.5
        pts[2][idx] += delta*0.5
        pts[3][idx] -= delta*0.5
        if collapse:
            del pts[collapse]
    except Exception:
        logger.warning('trapezoid pad has no rect_delta')

    pts.append(pts[0])
    return Part.makePolygon(pts)

def make_circle(size,params=None):
    _ = params
    segmetns = 10
    if segmetns == None:
        return Part.Wire(Part.makeCircle(size.x*0.5))
    else:
        edges = Part.makePolygon(Part.makeCircle(size.x*0.5).discretize(Number = round(segmetns) )).Edges
        return Part.Wire(edges) 

def make_oval(size,params=None):
    _ = params
    segmetns = 10
    if size.x == size.y:
        return make_circle(size)
    if size.x < size.y:
        r = size.x*0.5
        size.y -= size.x
        s  = ((0,0.5),(-0.5,0.5),(-0.5,-0.5),(0,-0.5),(0.5,-0.5),(0.5,0.5))
        a = (0,180,180,360)
    else:
        r = size.y*0.5
        size.x -= size.y
        s = ((-0.5,0),(-0.5,-0.5),(0.5,-0.5),(0.5,0),(0.5,0.5),(-0.5,0.5))
        a = (90,270,-90,-270)
    pts = [product(size,Vector(*v)) for v in s]
    
    if segmetns == None:
        return Part.Wire([
            Part.makeCircle(r,pts[0],Vector(0,0,1),a[0],a[1]),
            Part.makeLine(pts[1],pts[2]),
            Part.makeCircle(r,pts[3],Vector(0,0,1),a[2],a[3]),
            Part.makeLine(pts[4],pts[5])])
    else:
        edges = Part.makePolygon(Part.makeCircle(r,pts[0],Vector(0,0,1),a[0],a[1]).discretize(Number = round(segmetns / 2) )).Edges
        edges.extend(Part.makeLine(pts[1],pts[2]).Edges)
        edges.extend(Part.makePolygon(Part.makeCircle(r,pts[3],Vector(0,0,1),a[2],a[3]).discretize(Number = round(segmetns / 2) )).Edges)
        edges.extend(Part.makeLine(pts[4],pts[5]).Edges)
        return Part.Wire(edges)


def make_roundrect(size,params):
    rratio = 0.25
    segmetns = 10
    try:
        rratio = params.roundrect_rratio
        if rratio > 0.5:
            return make_oval(size)
    except Exception:
        logger.warning('round rect pad has no rratio')

    length = min(size.x, size.y)
    r = length*rratio
    n = Vector(0,0,1)
    sx = size.x*0.5
    sy = size.y*0.5

    rounds = [(r,False)]*4

    if 'chamfer_ratio' in params and 'chamfer' in params:
        ratio = params.chamfer_ratio
        if ratio < 0.0:
            ratio = 0.0
        elif ratio > 0.5:
            ratio = 0.5
        for i,corner in enumerate(('top_right',
                                    'top_left',
                                    'bottom_left',
                                    'bottom_right')):
            if corner in params.chamfer:
                rounds[i] = (ratio*length,True)

    edges = []

    r,chamfer = rounds[0]
    pstart = Vector(sx,sy-r)
    pt = pstart
    pnext = Vector(sx-r,sy)

    if r:
        if not chamfer:
            if segmetns == None:
                edges.append(Part.makeCircle(r,Vector(sx-r,sy-r),n,0,90))
            else:
                edges.extend(Part.makePolygon(Part.makeCircle(r,Vector(sx-r,sy-r),n,0,90).discretize(Number = round(segmetns/4))).Edges)
        else:
            edges.append(Part.makeLine(pt, pnext))

    r,chamfer = rounds[1]
    pt = pnext
    pnext = Vector(r-sx,sy)
    if pt != pnext:
        edges.append(Part.makeLine(pt,pnext))
        pt = pnext
    pnext = Vector(-sx,sy-r)

    if r:
        if not chamfer:
            if segmetns == None:
                edges.append(Part.makeCircle(r,Vector(r-sx,sy-r),n,90,180))
            else:
                edges.extend(Part.makePolygon(Part.makeCircle(r,Vector(r-sx,sy-r),n,90,180).discretize(Number = round(segmetns/4))).Edges)
        else:
            edges.append(Part.makeLine(pt,pnext))

    r,chamfer = rounds[2]
    pt = pnext
    pnext = Vector(-sx,r-sy)
    if pt != pnext:
        edges.append(Part.makeLine(pt,pnext))
        pt = pnext
    pnext = Vector(r-sx,-sy)

    if r:
        if not chamfer:
            if segmetns == None:
                edges.append(Part.makeCircle(r,Vector(r-sx,r-sy),n,180,270))
            else:
                edges.extend(Part.makePolygon(Part.makeCircle(r,Vector(r-sx,r-sy),n,180,270).discretize(Number = round(segmetns/4))).Edges)
        else:
            edges.append(Part.makeLine(pt,pnext))

    r,chamfer = rounds[3]
    pt = pnext
    pnext = Vector(sx-r,-sy)
    if pt != pnext:
        edges.append(Part.makeLine(pt,pnext))
        pt = pnext
    pnext = Vector(sx,r-sy)

    if r:
        if not chamfer:
            if segmetns == None:
                edges.append(Part.makeCircle(r,Vector(sx-r,r-sy),n,270,360))
            else:
                edges.extend(Part.makePolygon(Part.makeCircle(r,Vector(sx-r,r-sy),n,270,360).discretize(Number = round(segmetns/4))).Edges)
        else:
            edges.append(Part.makeLine(pt,pnext))

    pt = pnext
    if pt != pstart:
        edges.append(Part.makeLine(pt,pstart))

    return Part.Wire(edges)

#maui
def make_gr_poly(params):
    if hasattr(params.pts,"xy"):  #try:
        points = SexpList(params.pts.xy)
        # close the polygon
        points._append(params.pts.xy._get(0))

    # It seems kicad's polygon has inconsistent winding. Use Path.Area
    # projection to make sure we get the counter colockwise winding, otherwise
    # it will be interpreted as a hole.
    poly = Part.makePolygon([makeVect(p) for p in reversed(points)])
    try:
        area = Path.Area(Fill=True, FitArcs=False, Coplanar=0, Outline=True)
        area.add(poly)
        return area.getShape().Wire1
    except Exception as e:
        logger.warning(f'Failed to get outline of gr_poly: {str(e)}')
        return poly
# maui
def make_fp_poly(params):
    points = SexpList(params.pts.xy)
    # close the polygon
    points._append(params.pts.xy._get(0))
    # KiCAD polygon runs in clockwise, but FreeCAD wants CCW, so must reverse.
    return Part.makePolygon([makeVect(p) for p in reversed(points)])
# maui

def make_gr_line(params):
    return Part.makeLine(makeVect(params.start),makeVect(params.end))

def make_gr_arc(params):
    if hasattr(params, 'angle'):
        return  makeArc(makeVect(params.start),makeVect(params.end),params.angle)
    return Part.ArcOfCircle(makeVect(params.start),
                            makeVect(params.mid),
                            makeVect(params.end)).toShape()

def make_gr_curve(params):
    return makeCurve([makeVect(p) for p in SexpList(params.pts.xy)])

def make_gr_circle(params, width=0):
    center = makeVect(params.center)
    end = makeVect(params.end)
    r = center.distanceToPoint(end)
    if not width or r <= width*0.5:
        return Part.makeCircle(r+width*0.5, center)
    return Part.makeCompound([Part.Wire(Part.makeCircle(r+width*0.5,center)),
                              Part.Wire(Part.makeCircle(r-width*0.5,center,Vector(0,0,-1)))])
# maui
def make_gr_circle_outl(params, width=0):
    center = makeVect(params.center)
    end = makeVect(params.end)
    r = center.distanceToPoint(end)
    if not width or r <= width*0.5:
        return Part.makeCircle(r+width*0.5, center)
    return [Part.makeCircle(r+width*0.5, center), Part.makeCircle(r-width*0.5,center)]
    
# def make_gr_circle_outl(params, width=0):
#     center = makeVect(params.center)
#     end = makeVect(params.end)
#     r = center.distanceToPoint(end)
#     #print(r,width*0.5)
#     if (r <= abs(width*0.5) and width<0):
#         return None #Part.makeCircle(0.01, center)
#     else:
#         return Part.makeCircle(r+width*0.5, center)
#     #return Part.makeCompound([Part.Wire(Part.makeCircle(r+width*0.5,center)),
#     #                          Part.Wire(Part.makeCircle(r-width*0.5,center,Vector(0,0,-1)))])
# maui

def make_gr_rect(params):
    start = makeVect(params.start)
    end = makeVect(params.end)
    return Part.makePolygon([start, Vector(start.x, end.y), end, Vector(end.x, start.y), start])


def getLineWidth(param, default):
    width = getattr(param, 'width', None)
    if not width:
        if hasattr(param, 'stroke'):
            width = getattr(param.stroke, 'width', default)
        else:
            width = default
    return width


def makePrimitve(key, params):
    
    for param in SexpList(params):
        try:
            width = getLineWidth(param, 0)
            if width and key == 'gr_circle':
                return make_gr_circle(param, width), 0
            else:
                make_shape = globals()['make_{}'.format(key)]
                return make_shape(param), width
        except KeyError:
            logger.warning('Unknown primitive {} in custom pad'.format(key))
            return None, None

def makeThickLine(p1,p2,width):
    length = p1.distanceToPoint(p2)
    line = make_oval(Vector(length+2*width,2*width))
    p = p2.sub(p1)
    a = -degrees(DraftVecUtils.angle(p))
    line.translate(Vector(length*0.5))
    line.rotate(Vector(),Vector(0,0,1),a)
    line.translate(p1)
    return line

def makeArc(center,start,angle):
    p = start.sub(center)
    r = p.Length
    a = -degrees(DraftVecUtils.angle(p))
    # NOTE: KiCAD pcb geometry runs in clockwise, while FreeCAD is CCW. So the
    # resulting arc below is the reverse of what's specified in kicad_pcb
    if angle>0:
        arc = Part.makeCircle(r,center,Vector(0,0,1),a-angle,a)
        arc.reverse();
    else:
        arc = Part.makeCircle(r,center,Vector(0,0,1),a,a-angle)
    return arc

def makeCurve(poles):
    return Part.BSplineCurve(poles).toShape()

def findWires(edges):
    try:
        return [Part.Wire(e) for e in Part.sortEdges(edges)]
    except AttributeError:
        msg = 'Missing Part.sortEdges.'\
            'You need newer FreeCAD (0.17 git 799c43d2)'
        logger.error(msg)
        raise AttributeError(msg)

def getFaceCompound(shape,wire=False):
    objs = []
    for f in shape.Faces:
        selected = True
        for v in f.Vertexes:
            if not isZero(v.Z):
                selected = False
                break
        if not selected:
            continue

        ################################################################
        ## TODO: FreeCAD curve.normalAt is not implemented
        ################################################################
        # for e in f.Edges:
            # if isinstance(e.Curve,(Part.LineSegment,Part.Line)): continue
            # if not isZero(e.normalAt(Vector()).dot(Vector(0,0,1))):
                # selected = False
                # break
        # if not selected: continue

        if not wire:
            objs.append(f)
            continue
        for w in f.Wires:
            objs.append(w)
    if not objs:
        raise ValueError('null shape')
    return Part.makeCompound(objs)


def unpack(obj):
    if not obj:
        raise ValueError('null shape')

    if isinstance(obj,(list,tuple)) and len(obj)==1:
        return obj[0]
    return obj


def getKicadPath(env='', var_name='KICAD9_3DMODEL_DIR'):
    """Get KiCad path variables from configuration file"""
    confpath = ''
    if env:
        confpath = os.path.expanduser(os.environ.get(env,''))
        if not os.path.isdir(confpath):
            confpath=''
    if not confpath:
        if sys.platform == 'darwin':
            confpath = os.path.expanduser('~/Library/Preferences/kicad')
        elif sys.platform == 'win32':
            confpath = os.path.join(
                    os.path.abspath(os.environ['APPDATA']),'kicad')
        else:
            confpath=os.path.expanduser('~/.config/kicad')

    import re
    kicad_common = os.path.join(confpath,'kicad_common')
    if not os.path.isfile(kicad_common):
        kicad_common += ".json"
        if not os.path.isfile(kicad_common):
            subdir = None
            version = 0
            for dir in os.listdir(confpath):
                try:
                    if float(dir) > version:
                        version = float(dir)
                        subdir = dir
                except:
                    continue
            if subdir is None or version == 0:
                return None
            confpath = os.path.join(confpath, subdir)
            kicad_common = os.path.join(confpath, 'kicad_common')
            logger.info("Checking {}".format(kicad_common))
            if not os.path.isfile(kicad_common):
                kicad_common += ".json"
                if not os.path.isfile(kicad_common):
                    logger.warning('cannot find kicad_common')
                    return None
            logger.info("Found kicad_common at {}".format(kicad_common))
    
    try:
        with open(kicad_common,'r') as f:
            content = f.read()
    except Exception as e:
        logger.warning('Failed to read kicad_common: {}'.format(e))
        return None
    
    # Try to find the requested variable (support both old and new KiCad formats)
    patterns = [
        rf'^\s*"*{var_name}"*\s*[:=]\s*([^\r\n]+)',  # Current variable name
        r'^\s*"*KISYS3DMOD"*\s*[:=]\s*([^\r\n]+)',   # Legacy variable name
        r'^\s*"*KICAD6_3DMODEL_DIR"*\s*[:=]\s*([^\r\n]+)',  # KiCad 6
        r'^\s*"*KICAD7_3DMODEL_DIR"*\s*[:=]\s*([^\r\n]+)',  # KiCad 7
        r'^\s*"*KICAD8_3DMODEL_DIR"*\s*[:=]\s*([^\r\n]+)',  # KiCad 8
        r'^\s*"*KICAD9_3DMODEL_DIR"*\s*[:=]\s*([^\r\n]+)',  # KiCad 9
    ]
    
    for pattern in patterns:
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            path = unquote(match.group(1).rstrip(' "'))
            return path
    
    logger.warning('no 3D model path found in KiCad configuration')
    return None

_model_cache = {}

def clearModelCache():
    _model_cache = {}

def recomputeObj(obj):
    obj.recompute()
    obj.purgeTouched()

def expandKicadVars(filename):
    """Expand KiCad variables in filename by reading from KiCad configuration"""
    import re
    
    # First try standard environment variable expansion
    expanded = os.path.expandvars(filename)
    
    # If no variables were expanded (still contains ${...}), try KiCad config
    if '${' in expanded:
        # Look for KiCad variable patterns
        var_pattern = r'\$\{([^}]+)\}'
        matches = re.findall(var_pattern, expanded)
        
        for var_name in matches:
            var_value = None
            
            # Try to get from KiCad configuration for 3D model paths
            if var_name in ['KICAD9_3DMODEL_DIR', 'KICAD8_3DMODEL_DIR', 
                           'KICAD7_3DMODEL_DIR', 'KICAD6_3DMODEL_DIR', 'KISYS3DMOD']:
                var_value = getKicadPath(var_name=var_name)
            
            # If we still don't have a value, try the default 3D model path
            if not var_value and var_name in ['KICAD9_3DMODEL_DIR', 'KICAD8_3DMODEL_DIR', 
                                             'KICAD7_3DMODEL_DIR', 'KICAD6_3DMODEL_DIR', 'KISYS3DMOD']:
                var_value = getKicadPath()  # Use default lookup
            
            # If we found a value, replace it
            if var_value:
                expanded = expanded.replace('${' + var_name + '}', var_value)
                logger.info('Expanded ${{{}}}: {}'.format(var_name, var_value))
            else:
                logger.warning('Could not expand variable ${{{}}}'.format(var_name))
    
    return expanded

def loadModel(filename):
    mtime = None
    filename = expandKicadVars(filename)
    try:
        mtime = os.path.getmtime(filename)
        obj = _model_cache[filename]
        if obj[2] == mtime:
            logger.info('model cache hit');
            return obj
        else:
            logger.info('model reload due to time stamp change');
    except:
        pass
    # except OSError:
    #     return

    import Import
    doc = getActiveDoc()
    if not os.path.isfile(filename):
        return
    count = len(doc.Objects)
    dobjs = []
    try:
        Import.insert(filename,doc.Name)
        dobjs = doc.Objects[count:]
        obj = addObject(doc,'Part::Compound','tmp')
        setObjectLinks(obj, 'Links', dobjs)
        recomputeObj(obj)
        dobjs = [obj]+dobjs
        obj = (obj.Shape.copy())
        _model_cache[filename] = obj
        return obj
    except Exception as ex:
        logger.error('failed to load model: {}'.format(ex))
    finally:
        for o in dobjs:
            try:
                doc.removeObject(o.Name)
            except:
                pass

def get_mod_Ref(m):
        #if hasattr(m,'property'):
        if hasattr(m,'property'):
            for p in m.property: #kv8 fp field
                #print(str(p[0]),str(p[1]))
                if 'reference' in str(p[0]).lower():
                #    if 'reference' in str(p[1]).lower():
                    Ref = str(p[1]).strip('"')
                    #print (Ref)
                    #stop
                    return Ref
        if hasattr(m,'fp_text'):
            for p in m.fp_text: #kv7 fp field
                #print(str(p[0]),str(p[1]))
                if 'reference' in str(p[0]).lower():
                #    if 'reference' in str(p[1]).lower():
                    Ref = str(p[1]).strip('"')


class KicadFcad:
    def __init__(self,filename=None,debug=False,**kwds):

        #############################################################
        # Beginning of user customizable parameters during construction
        self.prefix = ''
        self.indent = '  '
        self.make_sketch = False
        self.sketch_use_draft = False
        self.sketch_radius_precision = -1
        self.holes_cache = {}
        self.workplane = {}
        self.active_doc_uuid = None
        self.sketch_constraint = True
        self.sketch_align_constraint = False
        self.merge_holes = not debug
        self.merge_vias = not debug
        self.merge_tracks = not debug
        self.zone_merge_holes = not debug
        self.merge_pads = not debug
        self.castellated = False
        self.refine = False
        self.arc_fit_accuracy = 0.0005
        self.layer_thickness = 0.01
        self.copper_thickness = 0.05
        self.board_thickness = None
        self.stackup = None
        self.quote_no_parse = None

        # set -1 to disable via in pads, 0 to enable as normal, >0 to use as
        # a ratio to via radius for creating a square to simplify via
        self.via_bound = 0

        # whether to skip via hole if there is via_bound
        self.via_skip_hole = None

        self.add_feature = True
        self.part_path = None
        self.path_env = 'KICAD_CONFIG_HOME'
        self.hole_size_offset = 0.0001
        self.pad_inflate = 0
        self.zone_inflate = 0
        self.nets = []
        if filename is None:
            filename = '/home/thunder/pwr.kicad_pcb'
        if not os.path.isfile(filename):
            raise ValueError("file not found");
        self.filename = filename
        self.colors = {
                'board':makeColor("0x3A6629"),
                'pad':{0:makeColor(219,188,126)}, # maui pads color
                'zone':{0:makeColor(0,80,0)},
                'track':{0:makeColor(0,120,0)},
                'copper':{0:makeColor(200,117,51)},
                'F.Cu':{0:makeColor(200,117,51)}, # maui start color
                'B.Cu':{0:makeColor(200,117,51)},
                'F.SilkS':{0:makeColor(0,0,255)},
                'B.SilkS':{0:makeColor(0,0,255)},
                'F.CrtYd':{0:makeColor(255,170,255)},
                'B.CrtYd':{0:makeColor(255,170,255)},
                'F.Fab':{0:makeColor(0,255,0)},
                'B.Fab':{0:makeColor(0,255,0)},
                'F.Adhes':{0:makeColor(0,127,255)},
                'B.Adhes':{0:makeColor(127,0,255)},
                'Edge.Cuts':{0:makeColor(255,0,0)},
                'Margin':{0:makeColor(255,170,170)},
                'holes':{0:makeColor(170,255,127)},
                'NetTie':{0:makeColor(255,114,12)}, 
                'Dwgs.User':{0:makeColor(188,188,188)}, # maui end color
                'Cmts.User':{0:makeColor(0,170,255)}, # maui end color
        }
        self.layer_type = 0
        self.layer_match = None
        self.encoding = 'utf-8'
        # Ending of user customizable parameters
        #############################################################

        # checking user overridden parameters
        for key,value in kwds.items():
            if not hasattr(self,key):
                raise ValueError('unknown parameter "{}"'.format(key))
            setattr(self,key,value)

        if not self.part_path:
            self.part_path = getKicadPath(self.path_env)
        self.pcb = KicadPCB.load(self.filename, self.quote_no_parse, self.encoding)

        if self.pcb._key == 'footprint':
            self.pcb._key = 'module'
        if self.pcb._key == 'module':
            self.module = self.pcb

            # this is a kicad_mod file, make it look like a kicad_pcb
            board = '''(kicad_pcb
                        (general
                            (thickness 0.3)
                            (drawings 0)
                            (tracks 0)
                            (zones 0)
                            (modules 1)
                            (nets 0)
                        )
                        (layers
                            (0 F.Cu signal)
                            (31 B.Cu signal)
                            (32 B.Adhes user)
                            (33 F.Adhes user)
                            (34 B.Paste user)
                            (35 F.Paste user)
                            (36 B.SilkS user)
                            (37 F.SilkS user)
                            (38 B.Mask user)
                            (39 F.Mask user)
                            (40 Dwgs.User user)
                            (41 Cmts.User user)
                            (42 Eco1.User user)
                            (43 Eco2.User user)
                            (44 Edge.Cuts user)
                            (45 Margin user)
                            (46 B.CrtYd user)
                            (47 F.CrtYd user)
                            (48 B.Fab user)
                            (49 F.Fab user)
                        )'''
            with open(self.filename) as f:
                board += f.read() + '\n)'

            self.pcb = KicadPCB(parseSexp(board, self.quote_no_parse))
        else:
            self.module = None

        if not self.board_thickness:
            try:
                self.board_thickness = self.pcb.general.thickness
            except Exception:
                pass
            if not self.board_thickness:
                self.board_thickness = 1.6 # maui

        self._dielectric_layers = []
        self._stackup_map = {}
        self._initStackUp()

        # stores layer name as read from the file, may contain quotes depending
        # on kicad version
        self.layer_name = ''

        # stores layer name without quote
        self.layer = ''

        self.setLayer(self.layer_type)

        if self.via_skip_hole is None and self.via_bound:
            self.via_skip_hole = True

        self._nets = set()
        self.net_names = dict()
        if 'net' in self.pcb:
            for n in self.pcb.net:
                self.net_names[n[0]] = n[1]
            self.setNetFilter(*self.nets)

        self.board_face = None
        self.board_uid = None

    def findLayer(self,layer, deftype=None):
        try:
            layer = int(layer)
        except:
            for layer_type in self.pcb.layers:
                name = self.pcb.layers[layer_type][0]
                if name==layer or unquote(name)==layer:
                    return (int(layer_type),name)
            if deftype is not None:
                return deftype, layer
            raise KeyError('layer {} not found'.format(layer))
        else:
            if str(layer) not in self.pcb.layers:
                if deftype is not None:
                    return deftype, str(layer)
                raise KeyError('layer {} not found'.format(layer))
            return (layer, self.pcb.layers[str(layer)][0])

    def setLayer(self,layer):
        self.layer_type, self.layer_name = self.findLayer(layer)
        self.layer = unquote(self.layer_name)
        if self.layer_type <= 31:
            self.layer_match = '*.Cu'
        else:
            self.layer_match = '*.{}'.format(self.layer.split('.')[-1])

    def _copperLayers(self):
        # If stackup is available, use it to find copper layers by type
        coppers = []
        if self.stackup:
            for entry in self.stackup:
                layer_name, offset, thickness, layer_type = entry
                if layer_type and 'copper' in layer_type.lower():
                    try:
                        layer_num, _ = self.findLayer(layer_name)
                        coppers.append((layer_num, layer_name))
                    except KeyError:
                        continue
        else:
            # Fallback to hardcoded layer numbers if no stackup
            coppers = [ (int(t),unquote(self.pcb.layers[t][0])) \
                        for t in self.pcb.layers if int(t)<=31]
        
        coppers.sort(key=lambda x : x[0])
        return coppers

    def _initStackUp(self):
        """Simple stackup initialization - processes layers in order, 
        accumulates thickness only when specified"""
        if self.stackup is None:
            self._log('Initializing stackup for kicad version={}', str(self.pcb.version), level='info')
            self.stackup = []
            stackup = getattr(getattr(self.pcb, 'setup', None), 'stackup', None)
            
            if stackup:
                current_z = 0.0
                
                # Process layers in order
                for layer in stackup.layer:
                    layer_name = unquote(layer[0])
                    layer_type, _ = self.findLayer(layer[0], 99)
                    layer_type_name = getattr(layer, 'type', None)
                    if layer_type_name:
                        layer_type_name = unquote(str(layer_type_name))
                    
                    # Get thickness if specified
                    thickness = getattr(layer, 'thickness', None)
                    if thickness is not None:
                        if isinstance(thickness, (list, tuple)):
                            thickness = float(thickness[0])
                        else:
                            thickness = float(thickness)
                    else:
                        thickness = 0.0
                    
                    # Add layer to stackup
                    self.stackup.append([layer_name, current_z, thickness, layer_type_name])
                    
                    # Move to next position (going down in Z)
                    current_z -= thickness
                
                # Adjust Z positions: find the lowest copper or dielectric layer
                copper_layers = [(i, item) for i, item in enumerate(self.stackup) 
                               if item[3] == "copper"]
                dielectric_layers = [(i, item) for i, item in enumerate(self.stackup) 
                                   if item[3] in ["core", "prepreg", "dielectric"]]
                
                adjustment = 0.0
                if copper_layers:
                    # Find lowest copper layer (most negative Z)
                    lowest_copper_idx, lowest_copper = min(copper_layers, key=lambda x: x[1][1])
                    lowest_copper_z = lowest_copper[1]
                    lowest_copper_thickness = lowest_copper[2]
                    # Adjust so lowest copper sits at its -thickness
                    adjustment = -(lowest_copper_z + lowest_copper_thickness)
                elif dielectric_layers:
                    # Find lowest dielectric layer (most negative Z)
                    lowest_dielectric_idx, lowest_dielectric = min(dielectric_layers, key=lambda x: x[1][1])
                    lowest_dielectric_z = lowest_dielectric[1]
                    # Adjust so lowest dielectric sits at z=0
                    adjustment = -lowest_dielectric_z
                
                # Apply adjustment to all layers
                if adjustment != 0.0:
                    for item in self.stackup:
                        item[1] += adjustment
                    self._log('Applied Z adjustment of {:.3f} to align layers', adjustment, level='info')
                    
                self._log('Processed {} layers from stackup', len(self.stackup), level='info')
            else:
                self._log('No stackup found in PCB setup', level='warning')
        
        # Build stackup map for layer lookup
        self._stackup_map = {}
        for item in self.stackup:
            layer_name = item[0]
            self._stackup_map[layer_name] = item
        
        # Calculate board thickness from copper layers only
        coppers = self._copperLayers()
        if len(coppers) > 1:
            copper_positions = []
            for _, name in coppers:
                if name in self._stackup_map:
                    z_pos = self._stackup_map[name][1]
                    copper_positions.append(z_pos)
            
            if copper_positions:
                self.board_thickness = max(copper_positions) - min(copper_positions)
        
        # Extract actual dielectric layers from the processed stackup
        self._dielectric_layers = []
        for item in self.stackup:
            layer_name, z_pos, thickness, layer_type = item
            if layer_type in ["core", "prepreg", "dielectric"] and thickness > 0:
                # Store as [z_position, thickness] for dielectric layers
                self._dielectric_layers.append([z_pos, thickness])

    def layerOffsets(self, thickness=None):
        coppers = self._copperLayers()
        offsets = dict()
        if not thickness or thickness == self.board_thickness:
            for _, name in coppers:
                offsets[name] = self._stackup_map[name][1]
            return offsets

        if len(coppers) == 1:
            offsets[coppers[0][1]] = 0
            return offsets
        step = (thickness + self.copper_thickness)/ (len(coppers)-1)
        offset = thickness
        for _,name in coppers:
            offsets[name] = offset
            offset -= step
        return offsets

    def setNetFilter(self,*nets):
        self._nets.clear()
        ndict = dict()
        nset = set()
        for n in self.pcb.net:
            ndict[n[1].strip('"')] = n[0]
            nset.add(n[0])

        for n in nets:
            try:
                self._nets.add(ndict[str(n)])
                continue
            except Exception:
                pass
            try:
                if int(n) in nset:
                    self._nets.add(int(n))
                    continue
            except Exception:
                pass
            logger.error('net {} not found'.format(n))

    def getNet(self,p):
        n = p.net
        return n if not isinstance(n,list) else n[0]

    def filterNets(self,p):
        try:
            return self._nets and self.getNet(p) not in self._nets
        except Exception:
            return bool(self._nets)

    def filterLayer(self,p):
        layers = []
        l = getattr(p, 'layers', [])
        if unquote(l) == 'F&B.Cu':  # maui
            layers.append('F.Cu')
            layers.append('B.Cu')
        else:
            layers = [unquote(s) for s in l]
        if hasattr(p, 'layer'):
            layers.append(unquote(p.layer))
        if not layers:
            self._log('no layers specified', level='warning')
            return True
        if self.layer not in layers \
                and self.layer_match not in layers \
                and '*' not in layers:
            self._log('skip layer {}, {}, {}',
                    self.layer, self.layer_match, layers, level='trace')
            return True

    def netName(self,p):
        try:
            return unquote(self.net_names[self.getNet(p)])
        except Exception:
            return 'net?'

    def _log(self,msg,*arg,**kargs):
        level = 'info'
        if kargs:
            if 'level' in kargs:
                level = kargs['level']
        if logger.isEnabledFor(level):
            getattr(logger,level)('{}{}'.format(self.prefix,msg.format(*arg)))


    def _pushLog(self,msg=None,*arg,**kargs):
        if msg:
            self._log(msg,*arg,**kargs)
        if 'prefix' in kargs:
            prefix = kargs['prefix']
            if prefix is not None:
                self.prefix = prefix
        self.prefix += self.indent


    def _popLog(self,msg=None,*arg,**kargs):
        self.prefix = self.prefix[:-len(self.indent)]
        if msg:
            self._log(msg,*arg,**kargs)

    def _makeLabel(self,obj,label):
        if self.layer:
            obj.Label = '{}#{}'.format(obj.Name,self.layer)
        if label is not None:
            obj.Label += '#{}'.format(label)

    def _makeObject(self,otype,name,
            label=None,links=None,shape=None):
        doc = getActiveDoc()
        obj = addObject(doc,otype,name)
        self._makeLabel(obj,label)
        if links is not None:
            setObjectLinks(obj, links, shape)
            # for s in shape if isinstance(shape,(list,tuple)) else (shape,):
            #     if hasattr(s,'ViewObject'):
            #         s.ViewObject.Visibility = False
            if hasattr(obj,'recompute'):
                recomputeObj(obj)
        return obj

    def _makeSketch(self,objs,name,label=None):
        if self.sketch_use_draft:
            import Draft
            getActiveDoc()
            nobj = Draft.makeSketch(objs,name=name,autoconstraints=True,
                delete=True,radiusPrecision=self.sketch_radius_precision)
            disableElementMapping(nobj)
            self._makeLabel(nobj,label)
            return nobj

        from Sketcher import Constraint

        StartPoint = 1
        EndPoint = 2

        doc = getActiveDoc()

        nobj = addObject(doc,"Sketcher::SketchObject", '{}_sketch'.format(name))
        self._makeLabel(nobj,label)
        # nobj.ViewObject.Autoconstraints = False

        radiuses = {}
        constraints = []

        def addRadiusConstraint(edge):
            try:
                if self.sketch_radius_precision<0:
                    return
                if self.sketch_radius_precision==0:
                    constraints.append(Constraint('Radius',
                            nobj.GeometryCount-1, edge.Curve.Radius))
                    return
                r = round(edge.Curve.Radius,self.sketch_radius_precision)
                constraints.append(Constraint('Equal',
                    radiuses[r], nobj.GeometryCount-1))
            except KeyError:
                radiuses[r] = nobj.GeometryCount-1
                constraints.append(Constraint('Radius',nobj.GeometryCount-1,r))
            except AttributeError:
                pass

        for obj in objs if isinstance(objs,(list,tuple)) else (objs,):
            if isinstance(obj,Part.Shape):
                shape = obj
            else:
                shape = obj.Shape
            norm = DraftGeomUtils.getNormal(shape)
            if not self.sketch_constraint:
                for wire in shape.Wires:
                    for edge in wire.OrderedEdges:
                        nobj.addGeometry(DraftGeomUtils.orientEdge(
                            edge,norm,make_arc=True))
                continue

            for wire in shape.Wires:
                last_count = nobj.GeometryCount
                edges = wire.OrderedEdges
                for edge in edges:
                    nobj.addGeometry(DraftGeomUtils.orientEdge(
                        edge,norm,make_arc=True))

                    addRadiusConstraint(edge)

                for i,g in enumerate(nobj.Geometry[last_count:]):
                    if edges[i].Closed:
                        continue
                    seg = last_count+i
                    if self.sketch_align_constraint:
                        if DraftGeomUtils.isAligned(g,"x"):
                            constraints.append(Constraint("Vertical",seg))
                        elif DraftGeomUtils.isAligned(g,"y"):
                            constraints.append(Constraint("Horizontal",seg))

                    if seg == nobj.GeometryCount-1:
                        if not wire.isClosed():
                            break
                        g2 = nobj.Geometry[last_count]
                        seg2 = last_count
                    else:
                        seg2 = seg+1
                        g2 = nobj.Geometry[seg2]

                    end1 = g.value(g.LastParameter)
                    start2 = g2.value(g2.FirstParameter)
                    if DraftVecUtils.equals(end1,start2) :
                        constraints.append(Constraint(
                            "Coincident",seg,EndPoint,seg2,StartPoint))
                        continue
                    end2 = g2.value(g2.LastParameter)
                    start1 = g.value(g.FirstParameter)
                    if DraftVecUtils.equals(end2,start1):
                        constraints.append(Constraint(
                            "Coincident",seg,StartPoint,seg2,EndPoint))
                    elif DraftVecUtils.equals(start1,start2):
                        constraints.append(Constraint(
                            "Coincident",seg,StartPoint,seg2,StartPoint))
                    elif DraftVecUtils.equals(end1,end2):
                        constraints.append(Constraint(
                            "Coincident",seg,EndPoint,seg2,EndPoint))

            if obj.isDerivedFrom("Part::Feature"):
                objs = [obj]
                while objs:
                    obj = objs[0]
                    objs = objs[1:] + obj.OutList
                    doc.removeObject(obj.Name)

        nobj.addConstraint(constraints)
        recomputeObj(nobj)
        return nobj

    def _makeCompound(self,obj,name,label=None,fit_arcs=False,
            fuse=False,add_feature=False,force=False):

        obj = unpack(obj)
        if not isinstance(obj,(list,tuple)):
            if not force and (
               not fuse or obj.TypeId=='Path::FeatureArea'):
                return obj
            obj = [obj]

        if fuse:
            return self._makeArea(obj,name,label=label,fit_arcs=fit_arcs)

        if add_feature or self.add_feature:
            return self._makeObject('Part::Compound',
                    '{}_combo'.format(name),label,'Links',obj)

        return Part.makeCompound(obj)


    def _makeArea(self,obj,name,offset=0,op=0,fill=None,label=None,
                force=False,fit_arcs=False,reorient=False,outline=False):
        if fill is None:
            fill = 2
        elif fill:
            fill = 1
        else:
            fill = 0

        if not isinstance(obj,(list,tuple)):
            obj = (obj,)

        if isinstance(obj[0], Part.Shape):
            shape = obj[0]
        else:
            shape = Part.getShape(obj[0])
        workplane = self.getWorkPlane(shape)

        if self.add_feature and name:
            if not force and obj[0].TypeId == 'Path::FeatureArea' and (
                obj[0].Operation == op or len(obj[0].Sources)==1) and \
                obj[0].Fill == fill:

                ret = obj[0]
                if len(obj) > 1:
                    ret.Sources = list(ret.Sources) + list(obj[1:])
            else:
                ret = self._makeObject('Path::FeatureArea',
                                        '{}_area'.format(name),label)
                ret.Accuracy = self.arc_fit_accuracy
                ret.Sources = obj
                ret.Operation = op
                ret.Fill = fill
                ret.Offset = offset
                ret.Coplanar = 0
                ret.WorkPlane = workplane
                ret.FitArcs = fit_arcs
                ret.Reorient = reorient
                ret.Outline = outline
                # for o in obj:
                #     o.ViewObject.Visibility = False

            recomputeObj(ret)
        else:
            ret = Path.Area(Fill=fill,
                            FitArcs=fit_arcs,
                            Coplanar=0,
                            Reorient=reorient,
                            Accuracy=self.arc_fit_accuracy,
                            Offset=offset,
                            Outline=outline)
            ret.setPlane(workplane)
            for o in obj:
                ret.add(o,op=op)
            ret = ret.getShape()
        return ret

    def getWorkPlane(self, shape):
        z = shape.Vertex1.Point.z
        workplane = self.workplane.get(z, None)
        if not workplane:
            workplane = self.workplane[z] = Part.makeCircle(1, Vector(0,0,z))
        return workplane

    def _makeWires(self,obj,name,offset=0,fill=False,label=None,
                   fit_arcs=False, outline=False):
        if self.add_feature and name:
            if self.make_sketch:
                obj = self._makeSketch(obj,name,label)
            elif isinstance(obj,Part.Shape):
                obj = self._makeObject('Part::Feature', '{}_wire'.format(name),
                        label,'Shape',obj)
            elif isinstance(obj,(list,tuple)):
                objs = []
                comp = []
                for o in obj:
                    if isinstance(o,Part.Shape):
                        comp.append(o)
                    else:
                        objs.append(o)
                if comp:
                    comp = Part.makeCompound(comp)
                    objs.append(self._makeObject('Part::Feature',
                            '{}_wire'.format(name),label,'Shape',comp))
                obj = objs

        if outline or fill or offset:
            return self._makeArea(obj,name,offset=offset,fill=fill,
                    fit_arcs=fit_arcs,label=label,outline=outline)
        else:
            return self._makeCompound(obj,name,label=label)


    def _makeSolid(self,obj,name,height,label=None,fit_arcs=True):

        obj = self._makeCompound(obj,name,label=label,
                                    fuse=True,fit_arcs=fit_arcs)

        if not self.add_feature:
            return obj.extrude(Vector(0,0,height))

        nobj = self._makeObject('Part::Extrusion',
                                    '{}_solid'.format(name),label)
        nobj.Base = obj
        nobj.Dir = Vector(0,0,height)
        # obj.ViewObject.Visibility = False
        recomputeObj(nobj)
        return nobj


    def _makeFuse(self,objs,name,label=None,force=False):
        obj = unpack(objs)
        if not isinstance(obj,(list,tuple)):
            if not force:
                return obj
            obj = [obj]

        name = '{}_fuse'.format(name)

        if self.add_feature:
            self._log('making fuse {}...',name)
            obj =  self._makeObject('Part::MultiFuse',name,label,'Shapes',obj)
            obj.Refine = self.refine
            self._log('fuse done')
            return obj

        solids = []
        for o in obj:
            solids += o.Solids;

        if solids:
            self._log('making fuse {}...',name)
            obj = solids[0].multiFuse(solids[1:])
            if self.refine:
                obj = obj.removeSplitter()
            self._log('fuse done')
            return obj


    def _makeCut(self,base,tool,name,label=None):
        base = self._makeFuse(base,name,label=label)
        tool = self._makeFuse(tool,'drill',label=label)
        name = '{}_drilled'.format(name)
        self._log('making cut {}...',name)
        if self.add_feature:
            cut = self._makeObject('Part::Cut',name,label=label)
            cut.Base = base
            cut.Tool = tool
            cut.Refine = self.refine
            # base.ViewObject.Visibility = False
            # tool.ViewObject.Visibility = False
            recomputeObj(cut)
            # cut.ViewObject.ShapeColor = base.ViewObject.ShapeColor
        else:
            cut = base.cut(tool)
            if self.refine:
                cut = cut.removeSplitter()
        self._log('cut done')
        return cut


    def _place(self,obj,pos,angle=None):
        if not obj.isDerivedFrom('App::DocumentObject'):
            if angle:
                obj.rotate(Vector(),Vector(0,0,1),angle)
            obj.translate(pos)
        else:
            r = Rotation(Vector(0,0,1),angle) if angle else Rotation()
            obj.Placement = Placement(pos,r)
            obj.purgeTouched()

    def _makeEdgeCuts(self, sexp, ctx, wires, non_closed, at=None, layers=None):
        if not layers:
            # default to layer Edge.Cuts - find the actual layer number
            try:
                edge_cuts_layer, _ = self.findLayer("Edge.Cuts")
                layers = [edge_cuts_layer]
            except Exception:
                # fallback to layer 25 if Edge.Cuts not found
                layers = [25]
        for l in layers:
            try:
                _,layer = self.findLayer(l)
            except Exception:
                continue
            self._makeShape(sexp, ctx, wires, non_closed, layer, at)

    def _makeShape(self, sexp, ctx, wires, non_closed=None, layer=None, at=None):
        edges = []

        if at:
            at, angle = getAt(at)
        else:
            angle = None

        for tp in 'line','arc','circle','curve','poly','rect':
            name = ctx + '_' + tp
            primitives = getattr(sexp, name, None)
            if not primitives:
                continue;
            primitives = SexpList(primitives)
            self._log('making {} {}s',len(primitives), tp)
            try: # maui
                make_shape = globals()['make_gr_{}'.format(tp)]
                if tp == 'poly': # maui
                    for l in primitives:
                        if not layer:
                            if self.filterNets(l) or self.filterLayer(l):
                                continue
                        elif l.layer != layer:
                            continue
                        shape = make_fp_poly(l)
                        if angle:
                            shape.rotate(Vector(),Vector(0,0,1),angle)
                        if at:
                            shape.translate(at)
                        #Part.show(shape)
                        ws = shape.Wires
                        for w_ in ws:
                            wires.append(w_)
                        #s = Part.Face(shape.Wires[0])
                        #Part.show(s)
                else:
                    for l in primitives:
                        if not layer:
                            if self.filterNets(l) or self.filterLayer(l):
                                continue
                        elif l.layer != layer:
                            continue
                        shape = make_shape(l)
                        if angle:
                            shape.rotate(Vector(),Vector(0,0,1),angle)
                        if at:
                            shape.translate(at)
                        width = getLineWidth(l, 1e-7)
                        edges += [[width, e] for e in shape.Edges]
            except Exception as e:# maui logging error
                # raise
                # traceback.print_exc() # maui 
                error_message = traceback.format_exc() #maui
                self._log('{}',error_message,level='error') # maui 
                pass  # maui logging error
        # The line width in edge cuts are important. When milling, the line
        # width can represent the diameter of the drill bits to use. The user
        # can use lines thick enough for hole cutting. In addition, the
        # endpoints of thick lines do not have to coincide to complete a loop.
        #
        # Therefore, we shall use the line width as tolerance to detect closed
        # wires. And for non-closed wires, if the shape_type is not wire, we
        # shall thicken the wire using Path.Area for hole cutting.

        for info in edges:
            w,e = info
            if w > 1e-7:
                e.fixTolerance(w)
            info += [e.firstVertex().Point,e.lastVertex().Point]

        while edges:
            w,e,pstart,pend = edges.pop(-1)
            wstart = wend = w
            elist = [(w,e)]
            if pstart.distanceToPoint(pend) <= (wstart+w)/2:
                closed = True
            else:
                closed = False
            i = 0
            while not closed and i < len(edges):
                w,e,ps,pe = edges[i]
                if pstart.distanceToPoint(ps) <= (wstart+w)/2:
                    e.reverse()
                    pstart = pe
                    wstart = w
                    elist.insert(0,(w,e))
                elif pstart.distanceToPoint(pe) <= (wstart+w)/2:
                    pstart = ps
                    wstart = w
                    elist.insert(0,(w,e))
                elif pend.distanceToPoint(ps) <= (wend+w)/2:
                    e.reverse()
                    pend = pe
                    wend = w
                    elist.append((w,e))
                elif pend.distanceToPoint(pe) <= (wend+w)/2:
                    pend = ps
                    wend = w
                    elist.append((w,e))
                else:
                    i += 1
                    continue
                edges.pop(i)
                i = 0
                if pstart.distanceToPoint(pend) <= (wstart+wend)/2:
                    closed = True
                    break

            wire = None
            try:
                #  tol = max([o[0] for o in elist])
                #  wire = Part.makeWires([disableTopoNaming(o[1]) for o in elist],'',tol,True)

                wire = Part.Wire([disableTopoNaming(o[1]) for o in elist])
                #  wire.fixWire(None,tol)
                #  wire.fix(tol,tol,tol)
            except Exception:
                pass

            if closed and (not wire or not wire.isClosed()):
                logger.warning('wire not closed')
                closed = False

            if wire and closed:
                wires.append(wire)
            elif non_closed is not None:
                for w,e in elist:
                    if w > 5e-7:
                        non_closed[w].append(e)
            else:
                for w,e in elist:
                    if w > 5e-7:
                        wires.append(self._makeWires(e, name=None, offset=w*0.5))

    def intersectBoard(self, objs, name, fit_arcs=True):
        if not objs:
            return objs
        if self.add_feature and self.board_uid != getActiveDoc().Uid:
            self.board_face = None
        if not self.board_face:
            self.board_face = self.makeBoard(shape_type='face', holes=False, single_layer=True)
            if self.add_feature:
                self.board_face.Visibility = False
                self.board_uid = self.board_face.Document.Uid

        objs = (self._makeCompound(objs,name,label='castellated'),self.board_face)
        # op=2 for intersection
        return self._makeArea(objs,name,op=2,label='castellated',fit_arcs=fit_arcs)

    def makeBoard(self,shape_type='solid',thickness=None,fit_arcs=True,
            holes=True, minHoleSize=0, ovalHole=True, prefix='', single_layer=False):

        non_closed = defaultdict(list)
        wires = []

        self._pushLog('making board...',prefix=prefix)
        self._makeEdgeCuts(self.pcb, 'gr', wires, non_closed)

        self._pushLog('checking footprints...',prefix=prefix)
        if self.module:
            # try Edge.Cuts first
            self._makeEdgeCuts(self.module, 'fp', wires, non_closed)
            # try F.CrtYd and B.CrtYd
            self._makeEdgeCuts(self.module, 'fp', wires, non_closed, layers=(46, 47))
        else:
            for m in self.pcb.module:
                self._makeEdgeCuts(m, 'fp', wires, non_closed, getattr(m, 'at', None))

        self._popLog()

        if not wires and not non_closed:
            if not wires and not non_closed:
                self._popLog('no board edges found')
                return

        def _addHoles(objs):
            h = self._cutHoles(None,holes,None,
                            minSize=minHoleSize,oval=ovalHole)
            if h:
                if isinstance(h,(tuple,list)):
                    objs += h
                elif holes:
                    objs.append(h)
            return objs

        def _wire():
            objs = []

            if wires:
                objs.append(self._makeWires(wires,'board'))

            for width,edges in non_closed.items():
                objs.append(self._makeWires(edges,'board',label=width,offset=width*0.5))

            return self._makeCompound(_addHoles(objs),'board')

        def _face():
            if not wires:
                raise RuntimeError('no closed wire')

            # Pick the wire with the largest area as outline
            areas = [ Part.Face(w).Area for w in wires ]
            outer = wires.pop(areas.index(max(areas)))

            objs = [ self._makeWires(outer,'board',label='outline') ]
            if wires:
                objs.append(self._makeWires(wires,'board',label='inner'))

            for width,elist in non_closed.items():
                wire = self._makeWires(elist,'board',label=width)
                # thicken non closed wire for hole cutting
                objs.append(self._makeArea(wire,'board',label=width,
                                           offset = width*0.5))

            return self._makeArea(_addHoles(objs),'board',
                            op=1,fill=True,fit_arcs=fit_arcs)

        base = []
        def _solid():
            base.append(_face())
            return self._makeSolid(base[0],'board',thickness,
                    fit_arcs = fit_arcs)

        if shape_type == 'solid' and not thickness and self._dielectric_layers:
            layers = self._dielectric_layers
        else:
            if not thickness:
                thickness = self.board_thickness
            layers = [(self.copper_thickness, thickness)]

        try:
            layer_save = self.layer
            self.layer = None
            try:
                func = locals()['_{}'.format(shape_type)]
            except KeyError:
                raise ValueError('invalid shape type: {}'.format(shape_type))

            thickness = layers[0][1]
            obj = func()
            # self.setColor(obj, 'board')

            if len(layers) > 1 and not single_layer:
                objs = [obj]
                for offset, t in layers[1:]:
                    if abs(t - layers[0][1]) < 1e-7:
                        if self.add_feature:
                            obj = self._makeObject('Part::Feature', 'board_solid')
                            obj.Shape = objs[0].Shape
                        else:
                            obj = objs[0].copy()
                    else:
                        obj = self._makeSolid(base[0], 'board', t)
                    self._place(obj,Vector(0,0,offset))
                    self.setColor(obj, 'board')
                    objs.append(obj)
                obj = self._makeCompound(objs, 'board')
                self.setColor(obj, 'board')
        finally:
            if layer_save:
                self.setLayer(layer_save)

        self._popLog('board done')
        fitView();
        return obj

    def makeHoles(self,shape_type='wire',minSize=0,maxSize=0,
            oval=False,prefix='',offset=0.0,npth=0,skip_via=False,
            board_thickness=None,extra_thickness=0.0,castellated=False):

        self._pushLog('making holes...',prefix=prefix)

        holes = defaultdict(list)
        ovals = defaultdict(list)

        width=0
        def _wire(obj,name,fill=False):
            return self._makeWires(obj,name,fill=fill,label=width)

        def _face(obj,name):
            return _wire(obj,name,True)

        def _solid(obj,name):
            return self._makeWires(obj,name,fill=True,label=width,fit_arcs=True)

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        oval_count = 0
        count = 0
        skip_count = 0
        if not offset:
            offset = self.hole_size_offset;

        thickness = board_thickness
        if not thickness:
            thickness = self.board_thickness
        layer_offsets = self.layerOffsets(thickness)
        z_offset = min(layer_offsets.values())

        for m in self.pcb.module:
            m_at,m_angle = getAt(m)
            for p in m.pad:
                if 'drill' not in p:
                    continue
                if self.filterNets(p):
                    skip_count += 1
                    continue
                if p[1]=='np_thru_hole':
                    if npth<0:
                        skip_count += 1
                        continue
                    ofs = abs(offset)
                else:
                    if npth>0:
                        skip_count += 1
                        continue
                    ofs = -abs(offset)
                if p.drill.oval:
                    if not oval:
                        continue
                    size = Vector(p.drill[0],p.drill[1])
                    w = make_oval(size+Vector(ofs,ofs))
                    ovals[min(size.x,size.y)].append(w)
                    oval_count += 1
                elif 0 in p.drill and \
                        p.drill[0]>=minSize and \
                        (not maxSize or p.drill[0]<=maxSize):
                    w = make_circle(Vector(p.drill[0]+ofs))
                    holes[p.drill[0]].append(w)
                    count += 1
                else:
                    skip_count += 1
                    continue
                at,angle = getAt(p)
                angle -= m_angle;
                if not isZero(angle):
                    w.rotate(Vector(),Vector(0,0,1),angle)
                w.translate(at)
                if m_angle:
                    w.rotate(Vector(),Vector(0,0,1),m_angle)
                m_at.z = z_offset
                w.translate(m_at)
        self._log('pad holes: {}, skipped: {}',count+skip_count,skip_count)
        if oval:
            self._log('oval holes: {}',oval_count)

        blind_holes = defaultdict(list)
        if npth<=0:
            via_skip = 0
            if skip_via or self.via_bound < 0:
                via_skip = len(self.pcb.via)
            else:
                ofs = -abs(offset)
                for v in self.pcb.via:
                    if self.filterNets(v):
                        via_skip += 1
                        continue
                    if hasattr(v,'drill'): # maui
                        if v.drill>=minSize and (not maxSize or v.drill<=maxSize):
        
                            z_offsets = [layer_offsets[unquote(n)] for n in v.layers]
                            pos = makeVect(v.at)
                            pos.z = min(z_offsets)
                            dist = max(z_offsets) - pos.z
        
                            s = v.drill+ofs
                            if self.via_bound:
                                s *= self.via_bound
                                w = make_rect(Vector(s,s))
                            else:
                                w = make_circle(Vector(s))
                            w.translate(pos)
                            if dist < thickness-0.001:
                                blind_holes[(pos.z,dist)].append(w)
                            else:
                                holes[v.drill].append(w)
                        else: # maui
                            via_skip += 1
                    else: # maui
                        via_skip += 1
                        self._log('drill missing', level='warning') #maui
            skip_count += via_skip
            self._log('via holes: {}, skipped: {}',len(self.pcb.via),via_skip)

            if blind_holes and shape_type != 'solid':
                self._log('skip blind via holes: {}',len(blind_holes))
                blind_holes = None

        self._log('total holes added: {}',
                count+oval_count+len(self.pcb.via)-skip_count)

        objs = []
        if blind_holes or holes or ovals:
            if self.merge_holes:
                for o in ovals.values():
                    objs += o
                for o in holes.values():
                    objs += o
                if objs:
                    objs = func(objs,"holes")
            else:
                for r in ((ovals,'oval'),(holes,'hole')):
                    if not r[0]:
                        continue
                    for (width,rs) in r[0].items():
                        objs.append(func(rs,r[1]))

            if not npth:
                label=None
            elif npth>0:
                label='npth'
            else:
                label='th'

            if castellated:
                objs = self.intersectBoard(objs, 'holes', fit_arcs=True)

            if shape_type != 'solid':
                if not objs:
                    self._popLog('no holes')
                    return
                objs = self._makeCompound(objs,'holes',label=label)
            else:
                if board_thickness:
                    thickness = board_thickness
                else:
                    thickness = self.board_thickness
                thickness += extra_thickness
                pos = -0.01
                if npth >= -1:
                    # through the whole board, must add top copper thickness,
                    # because 'board_thickness' does not include top copper
                    # thickness.
                    thickness += self._stackup_map['F.Cu'][2]
                if objs:
                    objs = self._makeSolid(objs,'holes',thickness,label=label)
                if blind_holes:
                    if not isinstance(objs, (tuple, list)):
                        objs = [objs] if objs else []
                    for (_,d),o in blind_holes.items():
                        if npth >= -1:
                            d += extra_thickness
                        objs.append(self._makeSolid(func(o,'blind'),'blind',d,label=label))
                    objs = self._makeCompound(objs,'holes',label=label)
                self._place(objs,FreeCAD.Vector(0,0,pos))

        if objs:  #maui
            self.setColor(objs,'holes')
        self._popLog('holes done')
        return objs


    def _cutHoles(self,objs,holes,name,label=None,fit_arcs=False,
                    minSize=0,maxSize=0,oval=True,npth=0,offset=0.0):
        if not holes:
            return objs

        if not isinstance(holes,(Part.Feature,Part.Shape)):
            hit = False
            if self.holes_cache is not None:
                key = '{}.{}.{}.{}.{}.{}.{}'.format(
                        self.add_feature,minSize,maxSize,oval,npth,offset,self.via_bound)
                doc = getActiveDoc();
                if self.add_feature and self.active_doc_uuid!=doc.Uid:
                    self.holes_cache.clear()
                    self.active_doc_uuid = doc.Uid

                try:
                    holes = self.holes_cache[key]
                    if self.add_feature:
                        # access the object's Name to make sure it is not
                        # deleted
                        self._log("fetch holes '{}' "
                            "from cache".format(holes.Name))
                    else:
                        self._log("fetch holes from cache")
                    hit = True
                except Exception:
                    pass

            if not hit:
                self._pushLog()
                holes = self.makeHoles(shape_type='wire',prefix=None,npth=npth,
                    minSize=minSize,maxSize=maxSize,oval=oval,offset=offset)
                self._popLog()

                if isinstance(self.holes_cache,dict):
                    self.holes_cache[key] = holes

        if not holes:
            return objs

        if not objs:
            return holes

        objs = (self._makeCompound(objs,name,label=label),holes)
        return self._makeArea(objs,name,op=1,label=label,fit_arcs=fit_arcs)

    def _makeCustomPad(self, params):
        wires = []
        anchor = getattr(getattr(params, 'options', None), 'anchor', None)
        if anchor in ('rect', 'circle'):
            w = globals()[f'make_{anchor}'](Vector(*params.size))
            wires.append(w)

        for key in params.primitives:
            primitives = SexpList(getattr(params.primitives, key))
            self._log(f'making {len(primitives)} {key}s')
            for param in primitives:
                wire,width = makePrimitve(key, param)
                if not width:
                    if isinstance(wire, Part.Edge):
                        wire = Part.Wire(wire)
                    wires.append(wire)
                elif not wire:
                    pass
                else:
                    wire = self._makeWires(wire, name=None, offset=width*0.5)
                    wires += wire.Wires
        if not wires:
            return
        if len(wires) == 1:
            return wires[0]
        return Part.makeCompound(wires)

    def getTrackPoints(self):
        points = set()
        for tp,ss in (('segment',self.pcb.segment), ('arc',getattr(self.pcb, 'arc', []))):
            for s in ss:
                if self.filterNets(s):
                    continue
                if unquote(s.layer) == self.layer:
                    points.add((s.start[0], s.start[1]))
                    points.add((s.end[0], s.end[1]))
        return points

    def makePads(self,shape_type='face',thickness=0.05,holes=False,
            fit_arcs=True,prefix='', z=None):

        self._pushLog('making pads...',prefix=prefix)

        def _wire(obj,name,label=None,fill=False):
            return self._makeWires(obj,name,fill=fill,label=label, offset=self.pad_inflate)

        def _face(obj,name,label=None):
            objs = _wire(obj,name,label,True)

            if not cut_wires and not cut_non_closed:
                return objs

            if not isinstance(objs, list):
                objs = [objs]

            inner_label = label + '_inner' if label else 'inner'
            if cut_wires:
                objs.append(self._makeWires(cut_wires,name,label=inner_label))

            for width,elist in cut_non_closed.items():
                l = '{}_{}'.format(inner_label, width)
                wire = self._makeWires(elist,name,label=l)
                # thicken non closed wire for hole cutting
                objs.append(self._makeArea(wire, name, label=l, offset = width*0.5))

            return self._makeArea(objs, name, op=1,fill=True)

        _solid = _face

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        objs = []
        track_points = None

        def filter_unconnected(v, at):
            if 'remove_unused_layers' in v:
                for s in getattr(v, 'zone_layer_connections', []):
                    try:
                        if self.layer_type == self.findLayer(s)[0]:
                            return
                    except Exception:
                        pass
                nonlocal track_points
                if track_points is None:
                    track_points = self.getTrackPoints()
                if not at in track_points:
                    return True

        count = 0
        pad_count = 0
        pad_locations = {}
        skip_count = 0
        for i,m in enumerate(self.pcb.module):
            ref = get_mod_Ref(m)
            # for t in m.property:
            #     if str(t[0]).strip('"') == 'Reference':
            #         ref = str(t[1]).strip('"')
            #         break
            m_at,m_angle = getAt(m)
            pads = []
            count += len(m.pad)

            cut_wires = []
            cut_non_closed = defaultdict(list)

            self._pushLog('checking edge cuts')
            self._makeEdgeCuts(m, 'fp', cut_wires, cut_non_closed)
            self._popLog()

            for j,p in enumerate(m.pad):
                if self.filterNets(p) or self.filterLayer(p):
                    skip_count+=1
                    continue

                shape = p[2]

                wp = ''
                if shape == 'custom':
                    w = self._makeCustomPad(p)
                    #Part.show(w)
                    # maui start
                    # print(p.size)
                    # print(p.options.anchor) #maui
                    make_shape = globals()['make_{}'.format(p.options.anchor)]
                    # print(make_shape)
                    wp = make_shape(Vector(*p.size),p)
                    if shape_type == 'wire':
                        # keeping visualization of internal reference pad ONLY for footprint loading
                        if w is not None:
                            w = Part.makeCompound([w,wp])
                            wp = ''
                        else:
                            w=wp
                    # Part.show(wp)
                    # maui end
                else:
                    try:
                        make_shape = globals()['make_{}'.format(shape)]
                    except KeyError:
                        raise NotImplementedError(
                                'pad shape {} not implemented\n'.format(shape))
                    w = make_shape(Vector(*p.size),p)

                if not w:
                    continue

                # kicad put pad shape offset inside drill element? Why?
                if 'drill' in p and 'offset' in p.drill:
                    w.translate(makeVect(p.drill.offset))
                    if wp != '': # maui
                        wp.translate(makeVect(p.drill.offset))
                    
                at,angle = getAt(p)
                angle -= m_angle;
                if not isZero(angle):
                    w.rotate(Vector(),Vector(0,0,1),angle)
                    if wp != '': # maui
                        wp.rotate(Vector(),Vector(0,0,1),angle)
                w.translate(at)
                if wp != '': # maui
                    wp.translate(at)
                if not self.merge_pads:
                    pads.append(func(w,'pad',
                        f'{i}#{j}#{p[0]}#{ref}#{self.netName(p)}#{shape}'))
                    if wp != '': # maui
                        pads.append(func(wp,'pad',
                            f'{i}#{j}#{p[0]}#{ref}#{self.netName(p)}#{shape}'))
                else:
                    pads.append(w)
                if z is not None:
                    # this is a hack to get the pad locations for the pads. 
                    # save the location of the pads in a dictionary
                    part = get_mod_Ref(m)    
                    try:
                        pin = p[0].strip('"')
                    except:
                        pin = p[0]
                    if pin == '1':
                        pin_1_loc = m_at                        
                      
                    location = FreeCAD.Placement()
                    corner1  = FreeCAD.Placement()
                    corner2  = FreeCAD.Placement()
                    location.Base = w.Placement.Base
                    corner1.Base = FreeCAD.Vector(w.BoundBox.XMin, w.BoundBox.YMin, 0)
                    corner2.Base = FreeCAD.Vector(w.BoundBox.XMax, w.BoundBox.YMax, 0)
                    # if the angle is zero do not rotate it 
                    if m_angle != 0:
                        # had to implement my own function. Becaseu freecad rotaion did not work as expected
                        # Rotation angle in radians
                        theta_radians = math.radians(m_angle)
                        x = location.Base.x
                        y = location.Base.y
                        x1 = corner1.Base.x
                        y1 = corner1.Base.y
                        x2 = corner2.Base.x
                        y2 = corner2.Base.y
 
                        # Recalculate using degrees
                        x_new_deg = x * math.cos(theta_radians) - y * math.sin(theta_radians)
                        y_new_deg = x * math.sin(theta_radians) + y * math.cos(theta_radians)
                        x1_new_deg = x1 * math.cos(theta_radians) - y1 * math.sin(theta_radians)
                        y1_new_deg = x1 * math.sin(theta_radians) + y1 * math.cos(theta_radians)
                        x2_new_deg = x2 * math.cos(theta_radians) - y2 * math.sin(theta_radians)
                        y2_new_deg = x2 * math.sin(theta_radians) + y2 * math.cos(theta_radians)
                        location.Base = FreeCAD.Vector(x_new_deg, y_new_deg, 0)
                        corner1.Base = FreeCAD.Vector(x1_new_deg, y1_new_deg, 0)
                        corner2.Base = FreeCAD.Vector(x2_new_deg, y2_new_deg, 0)
                    else:
                        location.rotate(Vector(), FreeCAD.Vector(0,0,1), m_angle)
                        corner1.rotate(Vector(), FreeCAD.Vector(0,0,1), m_angle)
                        corner2.rotate(Vector(), FreeCAD.Vector(0,0,1), m_angle)

                    location.translate(m_at)
                    corner1.translate(m_at)
                    corner2.translate(m_at)
                    location = [location.Base.x, location.Base.y, z]
                    corner1 = [corner1.Base.x, corner1.Base.y, z]
                    corner2 = [corner2.Base.x, corner2.Base.y, z]
                    bbox = [corner1[0], corner1[1], z, corner2[0], corner2[1], z]
     

                    if self.layer == 'F.Cu':
                        location[2] = z + thickness
                        bbox[2] = z + thickness
                        bbox[5] = z + thickness
                        pad_locations[f'{part}_{pin}_top'] = location
                        pad_locations[f'{part}_{pin}_top_bbox'] = bbox
                    elif self.layer == 'B.Cu':
                        pad_locations[f'{part}_{pin}_bottom'] = location
                        pad_locations[f'{part}_{pin}_bottom_bbox'] = bbox
                    pad_count += 1

            self._makeShape(m, 'fp', pads)

            if not pads:
                continue

            if not self.merge_pads:
                # maui start
                #print(pads)
                opads=[]
                for p in pads:
                    #print(p.TypeId) # wire -> 'Part::TopoShape'
                    if '<Wire object ' in str(p):
                        # shape = Part.makeFace(p,'Part::FaceMakerBullseye')
                        shape = Part.makeFace(p,'Part::FaceMakerSimple')
                        Part.show(shape)
                        s=FreeCAD.ActiveDocument.ActiveObject
                        opads.append(s)
                        #stop
                    else:
                        opads.append(p)
                obj = self._makeCompound(opads,'pads','{}#{}'.format(i,ref))
                #obj = self._makeCompound(pads,'pads','{}#{}'.format(i,ref))
                # maui end
            else:
                obj = func(pads,'pads','{}#{}'.format(i,ref))
            self._place(obj,m_at,m_angle)
            objs.append(obj)

        # if we call the function to make pads for the face, we save the pad locations
        if z is not None:
            # save the names and labels of the pads for future reference
            if self.layer == 'F.Cu':
                file = self.filename.split('.kicad_pcb')[0] + '_pad_locations_top.json'
            elif self.layer == 'B.Cu':
                file = self.filename.split('.kicad_pcb')[0] + '_pad_locations_bottom.json'
            else:
                file = 'pad_locations.json'
                print('Layer not recognized. Saving pad locations to pad_locations.json')
            with open(file, 'w') as f:
                json.dump(pad_locations, f, indent=4)
        cut_wires = None
        cut_non_closed = None

        via_skip = 0
        via_unconnected = 0
        vias = []
        if self.via_bound < 0:
            via_skip = len(self.pcb.via)
        else:
            for i,v in enumerate(self.pcb.via):
                layers = [self.findLayer(s)[0] for s in v.layers]
                if self.layer_type < min(layers)\
                        or self.layer_type > max(layers)\
                        or self.filterNets(v):
                    via_skip += 1
                    continue

                if filter_unconnected(v, (v.at[0], v.at[1])):
                    via_unconnected += 1
                    continue

                if self.via_bound:
                    w = make_rect(Vector(v.size*self.via_bound,v.size*self.via_bound))
                else:
                    w = make_circle(Vector(v.size))
                w.translate(makeVect(v.at))
                if not self.merge_vias:
                    vias.append(func(w,'via','{}#{}'.format(i,v.size)))
                else:
                    vias.append(w)

        if vias:
            if self.merge_vias:
                objs.append(func(vias,'vias'))
            else:
                objs.append(self._makeCompound(vias,'vias'))

        self._log('footprints: {}',len(self.pcb.module))
        self._log('pads: {}, skipped: {}',count,skip_count)
        self._log('vias: {}, skipped: {}, unconnected: {}',len(self.pcb.via),via_skip,via_unconnected)
        self._log('total pads added: {}',
                count-skip_count+len(self.pcb.via)-via_skip-via_unconnected)

        if objs:
            if self.castellated:
                objs = self.intersectBoard(objs, 'pads', fit_arcs=fit_arcs)
            objs = self._cutHoles(objs,holes,'pads',fit_arcs=fit_arcs)
            if shape_type=='solid':
                objs = self._makeSolid(objs,'pads', thickness,
                                    fit_arcs = fit_arcs)
            else:
                objs = self._makeCompound(objs,'pads',
                                    fuse=True,fit_arcs=fit_arcs)
            self.setColor(objs,'pad')

        self._popLog('pads done')
        fitView();
        return objs

# maui 
    def makeNetTies(self,shape_type='face',thickness=0.05,holes=False,
            fit_arcs=True,prefix=''):

        self._pushLog('making net ties...',prefix=prefix)

        def _wire(obj,name,label=None,fill=False):
            return self._makeWires(obj,name,fill=fill,label=label, offset=self.pad_inflate)

        def _face(obj,name,label=None):
            objs = _wire(obj,name,label,True)

            if not cut_wires and not cut_non_closed:
                return objs

            if not isinstance(objs, list):
                objs = [objs]

            inner_label = label + '_inner' if label else 'inner'
            if cut_wires:
                objs.append(self._makeWires(cut_wires,name,label=inner_label))

            for width,elist in cut_non_closed.items():
                l = '{}_{}'.format(inner_label, width)
                wire = self._makeWires(elist,name,label=l)
                # thicken non closed wire for hole cutting
                objs.append(self._makeArea(wire, name, label=l, offset = width*0.5))

            return self._makeArea(objs, name, op=1,fill=True)

        _solid = _face

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        objs = []
        ws=[]
        add_rot=0

        count = 0
        skip_count = 0
        for i,m in enumerate(self.pcb.module):
            ref = ''
            for t in m.fp_text:
                if t[0] == 'reference':
                    ref = t[1]
                    break;
            m_at,m_angle = getAt(m)
            nt = []
            count += len(m.fp_poly)

            cut_wires = []
            cut_non_closed = defaultdict(list)

            for j,pl in enumerate(m.fp_poly):
                if unquote(pl.layer) == self.layer:
                    shape='fp_poly'
                    nt = make_fp_poly(pl)
                if not nt:
                    continue
                else:
                    ws.append(nt)
            add_rot=0
            if hasattr(m, 'model'):
                #print(m.model[0].rotate.xyz[2])
                try:
                    add_rot=m.model[0].rotate.xyz[2]
                except:
                    pass

            if len (ws)>0:
                ws = _face(ws,'net-ties')
                if shape_type=='solid':
                    objs = self._makeSolid(ws,'net-ties', thickness,
                                        fit_arcs = fit_arcs)
                else:
                    objs = self._makeCompound(ws,'net-ties',
                                        fuse=True,fit_arcs=fit_arcs)
                self.setColor(objs,'NetTie')
            
        self._popLog('Net Tie poly done')
        fitView();
        return objs # obj  //additional rotation in deg
        

    def makeSketches(self, fit_arcs=True,prefix=''):

        self._pushLog('making sketches...',prefix=prefix)

        #def _wire(obj,name,label=None,fill=False):
        #    return self._makeWires(obj,name,fill=fill,label=label, offset=self.pad_inflate)

        width = 0
        def _line(edges,label,offset=0,fill=False):
            wires = findWires(edges)
            return self._makeWires(wires,label, offset=offset,
                    fill=fill, label=label, fit_arcs=fit_arcs)

        def _wire(edges,label,fill=False):
            return _line(edges,label,width*0.5,fill) #*0.5,fill)

        def _face(edges,label):
            return _wire(edges,label,True)

        _solid = _face        
        
        obj = []
        tbd = []

        count = 0
        skip_count = 0
        for i,m in enumerate(self.pcb.module):
            ref = ''
            for t in m.fp_text:
                if t[0] == 'reference':
                    ref = t[1]
                    break;
            m_at,m_angle = getAt(m)
            pads = []
            count += len(m.pad)

            cut_wires = []
            cut_non_closed = defaultdict(list)

            # self._pushLog('checking edge cuts')
            # self._makeEdgeCuts(m, 'fp', cut_wires, cut_non_closed)
            # self._popLog()
            ws=[]
            wst=[]
            objs=[]
            tl=None
            _solid = _face
            
            # test creating pads from gr_poly
            # print (' test creating pads from gr_poly')
            for j,p in enumerate(m.pad):            
                #print(unquote(p.layers))
                poly = []
                #print(self.layer,unquote(p.layers))
                if self.layer in str(unquote(p.layers)):
                    shape = p[2]
                    w = 0
                    if shape == 'custom':
                        w = self._makeCustomPad(p)
                    if not w:
                        continue            
                    # Part.show(w)
                    at,angle = getAt(p)
                    angle -= m_angle;
                    if not isZero(angle):
                        w.rotate(Vector(),Vector(0,0,1),angle)
                    w.translate(at)
    
                    poly.append(w)
                    self._makeShape(m, 'fp', poly)
        
                    if not poly:
                        continue
                    shape_type='solid'
                    try:
                        func = locals()['_{}'.format(shape_type)]
                    except KeyError:
                        raise ValueError('invalid shape type: {}'.format(shape_type))
                        
                    #print(poly)
                    for wr in poly:
                        objp = func(wr.Edges,'pads') #,'{}#{}'.format(i,ref))
                        self._place(objp,m_at,m_angle)
                        objs.append(objp)
            if len(objs)>0:
                for o in objs:
                    self.setPadColor(o,unquote(self.layer_name))
            #print (objs)
            # end test creating pads from gr_poly
                    
            for j,l in enumerate(m.fp_line):
                if unquote(l.layer) == self.layer:
                    #print(j,l)
                    try: #avoiding null lenght lines
                        ws.append((Part.Wire(make_gr_line(l))))
                        #wst.append(Part.Face(makeThickLine(makeVect(l.start),makeVect(l.end),l.width)))
                        #print ('l.width',hasattr(l,'width'))
                        #print ('l.stroke',hasattr(l,'stroke'))
                        if hasattr(l,'stroke'):
                            #print(l.stroke.width)
                            wst.append(makeThickLine(makeVect(l.start),makeVect(l.end),l.stroke.width/2.0))
                        else:
                            wst.append(makeThickLine(makeVect(l.start),makeVect(l.end),l.width/2.0))
                        #self._makeShape(m, 'fp', ws)
                    except:
                        pass
            for j,l in enumerate(m.fp_rect):
                if unquote(l.layer) == self.layer:
                    #print(j,l)
                    try: #avoiding null lenght lines
                        rc=Part.Wire(make_gr_rect(l))
                        ws.append(rc)
                        if hasattr(l,'stroke'):
                            #print(l.stroke.width)
                            width = l.stroke.width
                        else:
                            width = l.width
                        for e in rc.Edges:
                            wst.append(makeThickLine(makeVect([e.Vertexes[0].X,-e.Vertexes[0].Y]),makeVect([e.Vertexes[1].X,-e.Vertexes[1].Y]),width/2.0))
                    except:
                        pass
            for j,a in enumerate(m.fp_arc):
                if unquote(a.layer) == self.layer:
                    #print(j,l.start)
                    ac = Part.Wire(make_gr_arc(a))
                    ws.append((Part.Wire(make_gr_arc(a))))
                    if hasattr(a,'stroke'):
                        width = a.stroke.width
                    else:
                        width = a.width
                    aco=_wire(ac.Edges,self.layer)
                    wst.append(aco.Shape)
                    tbd.append(aco)
                    #doc=FreeCAD.ActiveDocument
                    #doc.recompute()
                    #try:
                    #    doc.removeObject(doc.getObject(aco.Name).Outlist[0].Name)
                    #except:
                    #    pass
                    #doc.removeObject(aco.Name)
                    #if hasattr(a, 'angle'):
                    #    wst.append(makeArc(makeVect(a.start),makeVect(a.end),a.angle))
                    #else:
                    #    wst.append(Part.ArcOfCircle(makeVect(a.start),makeVect(a.mid),makeVect(a.end)).toShape())
            for j,c in enumerate(m.fp_circle):
                if unquote(c.layer) == self.layer:
                    #print(j,l.start)
                    ws.append((Part.Wire(make_gr_circle(c))))
                    ##ws.append((Part.Wire(make_gr_circle(c).Edges)))
                    if hasattr(c,'stroke'):
                        cc=make_gr_circle_outl(c,c.stroke.width)
                    else:
                        cc=make_gr_circle_outl(c,c.width)
                    if isinstance(cc,list):
                        wst.append(Part.Wire(cc[0]))
                        wst.append(Part.Wire(cc[1]))
                    else:
                        wst.append(Part.Wire(cc))
                    #wst.append((Part.Wire(make_gr_circle(c,c.width)
                    
                    # Part.show(Part.Wire(make_gr_circle(c,c.width).Edges))
                    # wst.append((Part.Wire(make_gr_circle(c,c.width).Edges)))
                    if 0:
                        ce=make_gr_circle_outl(c,-c.width)
                        if ce is not None:
                            wst.append(Part.Wire(ce))
                        ci=Part.Wire(make_gr_circle_outl(c,+c.width))
                        wst.append(ci)
                    # try:
                    #     wst.append((Part.Wire(make_gr_circle(c,c.width))))
                    # except:
                    #     wst.append((make_gr_circle(c,c.width)))
                    #manca_thick_circle
            for j,pl in enumerate(m.fp_poly):
                if unquote(pl.layer) == self.layer:
                    pln=Part.Wire(make_gr_poly(pl))
                    ws.append((pln))
                    if hasattr(pl,'stroke'):
                        width = pl.stroke.width
                    else:
                        width = pl.width
                    for e in pln.Edges:
                        #aco=_wire(e,self.layer)
                        wst.append(makeThickLine(makeVect([e.Vertexes[0].X,-e.Vertexes[0].Y]),makeVect([e.Vertexes[1].X,-e.Vertexes[1].Y]),width/2.0))
                    #plno=_wire(pln.Edges,self.layer)
                    ##wst.append(pln)
                    #wst.append(plno.Shape)
            
            add_rot=0
            ## if hasattr(m, 'model'):
            ##     #print(m.model[0].rotate.xyz[2])
            ##     try:
            ##         add_rot=m.model[0].rotate.xyz[2]
            ##     except:
            ##         pass

            if len (wst)>0:
                Part.show(Part.makeCompound(wst))
                tl = FreeCAD.ActiveDocument.ActiveObject
                tl.Label = self.layer+'_outline_'
                self._place(tl,m_at,m_angle+add_rot)
                # del(wst)
            
            #Draft.make_sketch(ws, autoconstraints=False)
            #sp=FreeCAD.ActiveDocument.ActiveObject
            #sk = Draft.make_sketch(ws, autoconstraints=False)
            #sp.ViewObject.Visibility=False

            if not ws:
                continue

            obj = self._makeSketch(ws, self.layer_name)
            self._place(obj,m_at,m_angle+add_rot)
            obj.Label= self.layer+'_'
            #objs.append(obj)

        if obj:
            self.setSketchColor(obj,unquote(self.layer_name))

        self._popLog('sketch done')
        fitView();
        return obj,tl,tbd #,add_rot # obj, thicklines, to be deleted, additional rotation in deg

    def setSketchColor(self,obj,otype):
        if not self.add_feature:
            return
        try:
            color = self.colors[otype][self.layer_type]
        except KeyError:
            color = self.colors[otype][0]
        #print (color)
        #if 
        obj.ViewObject.LineColor = color

    def setPadColor(self,obj,otype):
        if not self.add_feature:
            return
        try:
            color = self.colors[otype][self.layer_type]
        except KeyError:
            color = self.colors[otype][0]
        #print (color)
        #if 
        obj.ViewObject.ShapeColor = color
# maui 

    def setColor(self,obj,otype):
        if not self.add_feature:
            return
        try:
            color = self.colors[otype][self.layer_type]
        except KeyError:
            color = self.colors[otype][0]
        if hasattr(obj.ViewObject,'MapFaceColor'):
            obj.ViewObject.MapFaceColor = False
        # obj.ViewObject.ShapeColor = color


    def makeTracks(self,shape_type='face',fit_arcs=True,
                    thickness=0.05,holes=False,prefix=''):

        self._pushLog('making tracks...',prefix=prefix)

        width = 0
        def _line(edges,label,offset=0,fill=False):
            wires = findWires(edges)
            return self._makeWires(wires,'track', offset=offset,
                    fill=fill, label=label, fit_arcs=fit_arcs)

        def _wire(edges,label,fill=False):
            return _line(edges,label,width*0.5,fill)

        def _face(edges,label):
            return _wire(edges,label,True)

        _solid = _face

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        tracks = defaultdict(lambda: defaultdict(list))
        count = 0
        for tp,ss in (('segment',self.pcb.segment), ('arc',getattr(self.pcb, 'arc', []))):
            for s in ss:
                if self.filterNets(s):
                    continue
                if unquote(s.layer) == self.layer:
                    if self.merge_tracks:
                        tracks[''][s.width].append((tp,s))
                    else:
                        tracks[self.netName(s)][s.width].append((tp,s))
                    count += 1

        objs = []
        i = 0
        for (name,sss) in tracks.items():
            for (width,ss) in sss.items():
                self._log('making {} tracks {} of width {:.2f}, ({}/{})',
                        len(ss),name,width,i,count)
                i+=len(ss)
                edges = []
                for tp,s in ss:
                    if tp == 'segment':
                        if s.start != s.end:
                            edges.append(Part.makeLine(
                                makeVect(s.start),makeVect(s.end)))
                        else:
                            self._log('Line (Track) through identical points {}',
                                    s.start, level="warning")
                    elif tp == 'arc':
                        if s.start == s.mid:
                            self._log('Arc (Track) with invalid point {}', s, level="warning")
                        elif s.start != s.end:
                            edges.append(Part.ArcOfCircle(
                                makeVect(s.end), makeVect(s.mid), makeVect(s.start)).toShape())
                        else:
                            start = makeVect(s.start)
                            middle = makeVect(s.mid)
                            r = start.distanceToPoint(middle)
                            edges.append(Part.makeCircle(r, (middle-start)/2))
                    else:
                        self._log('Unknown track type: {}', tp, level='warning')
                if self.merge_tracks:
                    label = '{}'.format(width)
                else:
                    label = '{}#{}'.format(width,name)
                objs.append(func(edges,label=label))

        if objs:
            if self.castellated:
                objs = self.intersectBoard(objs, 'tracks', fit_arcs=fit_arcs)
            objs = self._cutHoles(objs,holes,'tracks',fit_arcs=fit_arcs)

            if shape_type == 'solid':
                objs = self._makeSolid(objs,'tracks',thickness,
                                        fit_arcs=fit_arcs)
            else:
                objs = self._makeCompound(objs,'tracks',fuse=True,
                        fit_arcs=fit_arcs)

            self.setColor(objs,'track')

        self._popLog('tracks done')
        fitView();
        return objs

    def _makePolygons(self, fields, name, poly_holes,
            shape_type='face', thickness=0.05, prefix=''):

        if not fields:
            return []

        count = len(fields)
        self._pushLog(f'making {count} polygons...',prefix=prefix)

        def _wire(obj,fill=False):

            offset = self.zone_inflate + thickness*0.5

            if not poly_holes \
                    or (self.add_feature and self.make_sketch and self.zone_merge_holes):
                obj = [obj]+poly_holes
            elif poly_holes:
                obj = (self._makeWires(obj,f'{name}_outline'),
                       self._makeWires(poly_holes,f'{name}_hole'))
                return self._makeArea(obj,name,offset=offset, op=1, fill=fill)

            return self._makeWires(obj,name,fill=fill, offset=offset)


        def _face(obj):
            return _wire(obj,True)

        _solid = _face

        try:
            func = locals()['_{}'.format(shape_type)]
        except KeyError:
            raise ValueError('invalid shape type: {}'.format(shape_type))

        objs = []
        for idx,p in enumerate(fields):
            if (hasattr(p, 'layer') or hasattr(p, 'layers')) and self.filterLayer(p):
                continue
            poly_holes = []
            table = {}
            pts = SexpList(p.pts.xy)

            # close the polygon
            pts._append(p.pts.xy._get(0))

            # `table` uses a pair of vertex as the key to store the index of
            # an edge.
            for i in range(len(pts)-1):
                table[str((pts[i],pts[i+1]))] = i

            # This is how kicad represents holes in zone polygon
            #  ---------------------------
            #  |    -----      ----      |
            #  |    |   |======|  |      |
            #  |====|   |      |  |      |
            #  |    -----      ----      |
            #  |                         |
            #  ---------------------------
            # It uses a single polygon with coincide edges of oppsite
            # direction (shown with '=' above) to dig a hole. And one hole
            # can lead to another, and so forth. The following `build()`
            # function is used to recursively discover those holes, and
            # cancel out those '=' double edges, which will surely cause
            # problem if left alone. The algorithm assumes we start with a
            # point of the outer polygon.
            def build(start,end):
                results = []
                while start<end:
                    # We used the reverse edge as key to search for an
                    # identical edge of oppsite direction. NOTE: the
                    # algorithm only works if the following assumption is
                    # true, that those hole digging double edges are of
                    # equal length without any branch in the middle
                    key = str((pts[start+1],pts[start]))
                    try:
                        i = table[key]
                        del table[key]
                    except KeyError:
                        # `KeyError` means its a normal edge, add the line.
                        results.append(Part.makeLine(
                            makeVect(pts[start]),makeVect(pts[start+1])))
                        start += 1
                        continue

                    # We found the start of a double edge, treat all edges
                    # in between as holes and recurse. Both of the double
                    # edges are skipped.
                    h = build(start+1,i)
                    if h:
                        poly_holes.append(Part.Wire(h))
                    start = i+1
                return results

            edges = build(0,len(pts)-1)

            self._log('region {}/{}, holes: {}',idx+1,count,len(poly_holes))

            objs.append(func(Part.Wire(edges)))

            self._popLog()

        self._popLog(f'polygons done')
        return objs

    def makePolys(self,shape_type='face',thickness=0.05, fit_arcs=True, holes=False, prefix=''):
        '''For making outlier gr_poly as if it was zone, e.g. export from Gerber viewer
        '''
        poly_holes = []
        objs = self._makePolygons(getattr(self.pcb, 'gr_poly', None), 'poly',
                                    poly_holes, shape_type, thickness, prefix)
        if not objs:
            return

        objs = self._cutHoles(objs,holes,'polys')
        if shape_type == 'solid':
            objs = self._makeSolid(objs,'polys',thickness,fit_arcs=fit_arcs)
        else:
            objs = self._makeCompound(objs,'polys',
                            fuse=holes,fit_arcs=fit_arcs)
        self.setColor(objs,'zone')
        fitView();
        return objs

    def makeZones(self,shape_type='face',thickness=0.05, fit_arcs=True,
                    holes=False, prefix=''):

        self._pushLog('making zones...',prefix=prefix)

        z = None
        zone_holes = []
        objs = []
        for z in self.pcb.zone:
            if self.filterNets(z) or self.filterLayer(z):
                continue
            try: #maui
                objs += self._makePolygons(z.filled_polygon, 'zone', zone_holes,
                                       shape_type, thickness, prefix)
            except Exception as e:# maui logging error
                # traceback.print_exc() # maui 
                error_message = traceback.format_exc() #maui
                # self._log('{}',error_message,level='error') # maui 
                self._log('{}','error on \'self._makePolygons\' on Zones',level='warning') # maui
                pass  # maui logging error
                
        if objs:
            if self.castellated:
                objs = self.intersectBoard(objs, 'zones', fit_arcs=fit_arcs)
            objs = self._cutHoles(objs,holes,'zones')
            if shape_type == 'solid':
                objs = self._makeSolid(objs,'zones',thickness,fit_arcs=fit_arcs)
            else:
                objs = self._makeCompound(objs,'zones',
                                fuse=holes,fit_arcs=fit_arcs)
            self.setColor(objs,'zone')

        self._popLog('zones done')
        fitView();
        return objs


    def isBottomLayer(self):
        return self.layer_type == 31


    def makeCopper(self,shape_type='face',thickness=0.05,fit_arcs=True,
                    holes=False, z=0, prefix='',fuse=False, fea=False):

        self._pushLog('making copper layer {}...',self.layer,prefix=prefix)

        holes = self._cutHoles(None,holes,None)

        objs = []

        if shape_type=='solid':
            solid = True
            sub_fit_arcs = fit_arcs
            if fuse:
                shape_type = 'face'
        else:
            solid = False
            sub_fit_arcs = False
        for (name,offset) in (('Pads',thickness),
                              ('Tracks',0.5*thickness),
                              ('Zones',0),
                              ('Polys',thickness)):
            
            print('Making ', name, "in layer ", self.layer, 'with thickness ', thickness)
            if shape_type == 'solid' and name == 'Zones':
                th =  thickness
            else:
                th = z 
            obj = getattr(self,'make{}'.format(name))(fit_arcs=sub_fit_arcs,
                        holes=holes,shape_type=shape_type,prefix=None,
                        thickness=th)
            if not obj:
                continue
            if shape_type=='solid':
                ofs = offset if self.layer_type < 16 else -offset
                # self._place(obj,Vector(0,0,ofs))
            objs.append(obj)

        if not objs:
            return

        if shape_type=='solid':
            self._log("making solid")
            obj = self._makeCompound(objs,'copper')
            self._log("done solid")
        else:
            obj = self._makeArea(objs,'copper',fit_arcs=fit_arcs)
            self.setColor(obj,'copper')
            if solid:
                self._log("making solid")
                obj = self._makeSolid(obj,'copper',thickness)
                self._log("done solid")
                self.setColor(obj,'copper')

        self._place(obj,Vector(0,0,z))
        self._popLog('done copper layer {}',self.layer)

        # make the pads on the forward layer and bottom layer
        if solid and fea and ('F.Cu' in self.layer_name or 'B.Cu' in self.layer_name):
            name = 'Pads'
            offset = thickness if unquote(self.layer_name) == 'F.Cu' else -thickness
            shape_type = 'solid'
            tmp = self.via_bound
            self.via_bound = -1
            pads = getattr(self,'make{}'.format(name))(fit_arcs=sub_fit_arcs,
                    holes=holes,shape_type=shape_type,prefix=None,
                    thickness=thickness, z=z)
            if pads:
                self.via_bound = tmp
                self._place(pads,Vector(0,0,offset + z))
                self._popLog('done making fea pads {}',self.layer)
                return obj, pads
            else:
                self.via_bound = tmp
                self._popLog('no fea pads found')
                return obj, None
        fitView();
        return obj

    def makeCoppers(self,shape_type='face',fit_arcs=True,prefix='',
            holes=False,board_thickness=None,thickness=None,fuse=False, fea=False):

        self._pushLog('making all copper layers...',prefix=prefix)

        layer_save = self.layer
        objs = []
        pads = []
        layers = []
        thicknesses = []
        offsets = []

        if not board_thickness or not thickness:
            for layer, name in self._copperLayers():
                layers.append(layer)
                _,offset, t, _ = self._stackup_map[name]
                offsets.append(offset)
                thicknesses.append(t)
        else:
            for layer, name in self._copperLayers():
                layers.append(layer)
                if not hasattr(thickness,'get'):
                    thicknesses.append(float(thickness))
                else:
                    for key in (layer, str(layer), name, None, ''):
                        try:
                            thicknesses.append(float(thickness.get(key)))
                            break
                        except Exception:
                            pass
                if not len(layers) == len(thicknesses):
                    raise RuntimeError('No copper thickness found for layer ' % name)

            if len(layers) == 1:
                z_step = 0
            else:
                z_step = (board_thickness+thicknesses[-1])/(len(layers)-1)
            offsets = [ board_thickness - i*z_step for i,_ in enumerate(layers) ]

        thickness = max(thicknesses)

        if not layers:
            raise ValueError('no copper layer found')

        if not holes:
            hole_shapes = None
        elif fuse:
            # make only npth holes
            hole_shapes = self._cutHoles(None,holes,None,npth=1)
        else:
            hole_shapes = self._cutHoles(None,holes,None, npth=1)

        try:
            for layer,t,z in zip(layers, thicknesses, offsets):
                self.setLayer(layer)
                copper = self.makeCopper(shape_type,thickness = t, fit_arcs=fit_arcs,
                                    holes=hole_shapes,z=z,prefix=None,fuse=fuse, fea = fea)
                if copper:
                    # when the solid is set but the fuse is not the resulting object is a compound
                    if shape_type == 'solid' and fuse and fea and ('F.Cu' in self.layer_name or 'B.Cu' in self.layer_name):
                        if isinstance(copper, tuple) and len(copper) > 1:
                            objs.append(copper[0])
                            if copper[1] is not None:  # Only append pad if it exists
                                pads.append(copper[1])
                        else:
                            objs.append(copper)  # Just append the single copper object
                    else:
                        if not isinstance(copper, list):
                            copper = [copper]
                        for obj in copper:
                            if obj.TypeId == 'Part::Compound':
                                for sub_obj in obj.OutList:
                                    self._place(sub_obj, Vector(0, 0, z))
                                    objs.append(sub_obj)
                            else:
                                objs.append(obj)

        finally:
            if layer_save:
                self.setLayer(layer_save)

        if not objs:
            self._popLog('no copper found')
            return

        if shape_type=='solid':
            # make copper for plated through holes
            hole_coppers = self.makeHoles(shape_type='solid',prefix=None,
                oval=True,npth=-2,board_thickness=board_thickness,
                extra_thickness=0,castellated=self.castellated)
            if hole_coppers:
                self.setColor(hole_coppers,'copper')
                if self.add_feature:
                    self._place(hole_coppers,FreeCAD.Vector(0,0,thickness))
                else:
                    self._place(hole_coppers,FreeCAD.Vector(0,0,thickness + 0.001)) #to make up for the offset in the code for making holes. Not sure why is there. 
                objs.append(hole_coppers);

            if fuse:
                # connect coppers with pad with plated through holes, and fuse
                objs = self._makeFuse(objs,'coppers')
                self.setColor(objs,'copper')

            if holes:
                # make plated through holes with inward offset
                drills = self.makeHoles(shape_type='solid',prefix=None,
                        board_thickness=board_thickness,extra_thickness=4.5*thickness,
                        oval=True,npth=-1,offset=thickness,
                        skip_via=self.via_skip_hole)
                if drills:
                    self._place(drills,FreeCAD.Vector(0,0,-2.1*thickness))
                    if fuse:   
                        objs = self._makeCut(objs,drills,'coppers')
                        self.setColor(objs,'copper')
                        if fea and pads:
                            pads = self._makeCut(pads,drills,'pads')
                            objs = [objs,pads]
                        else:
                            objs = [objs]    
                    else:                        
                        tmp_objs = []
                        for obj in objs:
                            print(obj.Label)
                            tmp_obj = self._makeCut(obj,drills,obj.Label)
                            self.setColor(tmp_obj,'copper')
                            tmp_objs.append(tmp_obj)
                        objs = tmp_objs


        self._popLog('done making all copper layers')
        fitView();
        return objs


    def loadParts(self,z=0,combo=False,prefix=''):
        if not os.path.isdir(self.part_path):
            raise Exception('cannot find kicad package3d directory')

        self._pushLog('loading parts on layer {}...',self.layer,prefix=prefix)
        self._log('Kicad package3D path: {}',self.part_path)

        at_bottom = self.isBottomLayer()
        if z == 0:
            if at_bottom:
                z = -0.1
            else:
                z = self.pcb.general.thickness + 0.1

        if self.add_feature or combo:
            parts = []
        else:
            parts = {}

        for (module_idx,m) in enumerate(self.pcb.module):
            if unquote(m.layer) != self.layer:
                continue
            ref = get_mod_Ref(m)
            value = '?'
            for t in m.property:
                # if str(t[0]).strip('"') == 'Reference':
                #     ref = str(t[1]).strip('"')
                if str(t[0]).strip('"') == 'Value':
                    value = str(t[1]).strip('"')

            m_at,m_angle = getAt(m)
            m_at += Vector(0,0,z)
            objs = []
            for (model_idx,model) in enumerate(m.model):

                # the model path is stored in the first element of the model list
                path = model[0].strip('"')
                # remove .wrl, .stp, .step and .STP, .STEP from the end of the string. so we are left with the path to the file without the extension
                path = re.sub(r'\.(wrl|stp|step)$', '', path, flags=re.IGNORECASE)

                self._log('loading model {}/{} {} {} {}...',
                        model_idx,len(m.model), ref,value,model[0])
                for e in ('.stp','.STP','.step','.STEP'):
                    filename = os.path.join(path+e)
                    mobj = loadModel(filename)
                    if mobj is None:
                        continue
                    at = product(Vector(*model.offset.xyz),Vector(25.4,25.4,25.4))
                    rot = [-float(v) for v in reversed(model.rotate.xyz)]
                    pln = Placement(at,Rotation(*rot))
                    if not self.add_feature:
                        if combo:
                            obj = mobj[0].copy()
                            obj.Placement = pln
                        else:
                            obj = {'shape':mobj.copy()}
                            obj['shape'].Placement = pln
                        objs.append(obj)
                    else:
                        obj = self._makeObject('Part::Feature','model',
                            label='{}#{}#{}'.format(module_idx,model_idx,ref),
                            links='Shape',shape=mobj)
                        # obj.ViewObject.DiffuseColor = mobj[1]
                        obj.Placement = pln
                        objs.append(obj)
                    self._log('loaded')
                    break

            if not objs:
                continue

            pln = Placement(m_at,Rotation(Vector(0,0,1),m_angle))
            if at_bottom:
                pln = pln.multiply(Placement(Vector(),
                                    Rotation(Vector(1,0,0),180)))

            label = '{}#"{}"'.format(module_idx,ref)
            if self.add_feature or combo:
                obj = self._makeCompound(objs,'part',label,force=True)
                obj.Placement = pln
                parts.append(obj)
            else:
                parts[label] = {'pos':pln, 'models':objs}

        if parts:
            if combo:
                parts = self._makeCompound(parts,'parts')
            elif self.add_feature:
                grp = self._makeObject('App::DocumentObjectGroup','parts')
                for o in parts:
                    grp.addObject(o)
                parts = grp

        self._popLog('done loading parts on layer {}',self.layer)
        fitView();
        return parts


    def loadAllParts(self,combo=False):
        logger.info("Loading parts...")
        layer = self.layer
        objs = []
        layers = []
        offsets = []
        thicknesses = []
        try:
            for layer, name in self._copperLayers():
                layers.append(layer)
                _,offset, t, _= self._stackup_map[name]
                offsets.append(offset)
                thicknesses.append(t)
        except:
            pass
        try:
            self.setLayer(0)
            offset = offsets[0] + 2 * thicknesses[0] if len(offsets) > 0 else 0
            objs.append(self.loadParts(z = offset, combo=combo))
        except Exception as e:
            self._log('{}',e,level='error')
        try:
            self.setLayer(31)
            offset = offsets[-1] - thicknesses[-1] if len(offsets) > 0 else 0
            objs.append(self.loadParts(z = offset, combo=combo))
        except Exception as e:
            self._log('{}',e,level='error')
        finally:
            self.setLayer(layer)
        fitView();
        return objs


    def make(self,copper_thickness=0.05,fit_arcs=True,load_parts=False,
            board_thickness=None, combo=True, fuseCoppers=False):

        self._pushLog('making pcb...',prefix='')

        if combo > 1:
            fuseCoppers = True

        objs = []
        board = self.makeBoard(prefix=None,thickness=board_thickness)
        if board:
            objs.append(board)

        coppers = self.makeCoppers(shape_type='solid',holes=True,prefix=None,
                fit_arcs=fit_arcs,thickness=copper_thickness,fuse=fuseCoppers,
                board_thickness=board_thickness)

        if coppers:
            if not fuseCoppers:
                objs += coppers
            else:
                objs.append(coppers)

        if load_parts:
            objs += self.loadAllParts(combo=True)

        if combo:
            layer = self.layer
            try:
                self.layer = None
                if combo > 1:
                    objs = self._makeFuse(objs,'pcb')
                else:
                    objs = self._makeCompound(objs,'pcb')
                if self.add_feature and load_parts:
                    try:
                        objs.ViewObject.SelectionStyle = 1
                    except Exception:
                        pass
            finally:
                self.setLayer(layer)

        self._popLog('all done')
        fitView();
        return objs

def getTestFile(name):
    import glob
    if not os.path.exists(name):
        path = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(path,'tests')
        if name:
            path = os.path.join(path,name)
    else:
        path = name
    if os.path.isdir(path):
        return glob.glob(os.path.join(path,'*.kicad_pcb'))
    if os.path.isfile(path):
        return [path]
    path += '.kicad_pcb'
    if os.path.isfile(path):
        return [path]
    raise RuntimeError('Cannot find {}'.format(name))

def test(names=''):
    if not isinstance(names,(tuple,list)):
        names = [names]
    files = set()
    for name in names:
        files.update(getTestFile(name))
    for f in files:
        pcb = KicadFcad(f)
        pcb.make()
        pcb.make(fuseCoppers=True)
        pcb.add_feature = False
        Part.show(pcb.make())

