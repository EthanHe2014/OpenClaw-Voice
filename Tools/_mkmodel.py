import math

R = 15.0    # ball radius mm -> 30mm diameter
H = 1.5     # hole radius mm -> 3mm bore (matchstick)
nlat, nlon = 40, 64

def pt(th, ph):
    return (R*math.sin(th)*math.cos(ph), R*math.cos(th), R*math.sin(th)*math.sin(ph))

verts = []
for i in range(nlat+1):
    th = math.pi * i / nlat
    for j in range(nlon):
        ph = 2*math.pi * j / nlon
        verts.append(pt(th, ph))

def vid(i,j):
    return i*nlon + (j % nlon)

tris = []
for i in range(nlat):
    for j in range(nlon):
        a,b = vid(i,j), vid(i,j+1)
        c,d = vid(i+1,j), vid(i+1,j+1)
        tris.append((a,b,d)); tris.append((a,d,c))

with open("/Users/Ethan/Desktop/globe_ball_hole.stl","wb") as f:
    f.write(b"solid globe\n")
    for (a,b,c) in tris:
        f.write(b"  facet normal 0 0 0\n    outer loop\n")
        for v in (verts[a],verts[b],verts[c]):
            f.write(("      vertex %.6f %.6f %.6f\n" % v).encode())
        f.write(b"    endloop\n  endfacet\n")
    y0 = math.sqrt(R*R - H*H)
    nb = 48
    for j in range(nb):
        a0 = 2*math.pi*j/nb; a1 = 2*math.pi*(j+1)/nb
        p0 = (H*math.cos(a0), y0, H*math.sin(a0))
        p1 = (H*math.cos(a1), y0, H*math.sin(a1))
        p2 = (H*math.cos(a1), -y0, H*math.sin(a1))
        p3 = (H*math.cos(a0), -y0, H*math.sin(a0))
        for tri in [(p0,p1,p2),(p0,p2,p3)]:
            f.write(b"  facet normal 0 0 0\n    outer loop\n")
            for v in tri:
                f.write(("      vertex %.6f %.6f %.6f\n" % v).encode())
            f.write(b"    endloop\n  endfacet\n")
    f.write(b"endsolid globe\n")
print("STL written R=15mm bore=3mm")
