"""Read the supplied map's georeferenced relief and boundary as data only."""
import base64
import io
import json
import re

import numpy as np
from PIL import Image


class SourceRelief:
    def __init__(self, path, terrain, nodes):
        self.path = path
        text = path.read_text(encoding='utf-8')

        def literal(name):
            match = re.search(r'\bconst\s+' + re.escape(name) + r'\s*=\s*', text)
            if not match:
                raise ValueError(f'原始地图缺少{name}')
            # Parse JSON only. Never execute the attachment's JavaScript.
            return json.JSONDecoder().raw_decode(text[match.end():])[0]

        url = literal('terrainUrl')
        if not url.startswith('data:image/png;base64,'):
            raise ValueError('原始地形纹理不是内嵌PNG')
        self.rgb = np.asarray(Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1], validate=True))).convert('RGB')) / 255.
        self.geo_bounds = literal('terrainBounds')
        west, south, east, north = self.geo_bounds
        if not west < east or not south < north:
            raise ValueError('原始地形范围错误')
        self.terrain = terrain
        x, y = self.xy(np.array([west, east]), np.array([south, north]))
        self.extent = (float(x[0]), float(x[1]), float(y[0]), float(y[1]))
        self.geojson = literal('boundary')
        self.rings = []
        for feature in self.geojson['features']:
            geom = feature['geometry']
            if geom['type'] not in ('Polygon', 'MultiPolygon'):
                raise ValueError('乡界必须为多边形')
            polygons = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
            for polygon in polygons:
                for ring in polygon:
                    ring = np.asarray(ring, dtype=float)
                    if not np.array_equal(ring[0], ring[-1]):
                        raise ValueError('乡界环未闭合')
                    self.rings.append(np.column_stack(self.xy(ring[:, 0], ring[:, 1])))
        source_nodes = {}
        for key, field in [('dispatchCenter', '调度中心编号'), ('services', '服务区编号')]:
            for f in literal(key)['features']:
                source_nodes[f['properties'][field]] = f['geometry']['coordinates']
        if set(source_nodes) != set(nodes):
            raise ValueError('底图与节点表的节点集合不同')
        for key, node in nodes.items():
            if not np.allclose(source_nodes[key], [node.lon, node.lat], rtol=0, atol=1e-10):
                raise ValueError(f'底图节点{key}与原始节点表不一致')
        if not (terrain.lon0-terrain.sx/2 <= west < east <= terrain.lon0+(terrain.cols-.5)*terrain.sx
                and terrain.lat0-(terrain.rows-.5)*terrain.sy <= south < north <= terrain.lat0+terrain.sy/2):
            raise ValueError('底图范围超出DEM')
        self.metadata = {'source': str(path), 'texture_size': list(self.rgb.shape[:2]),
                         'geographic_extent_wsen': self.geo_bounds, 'boundary_rings': len(self.rings),
                         'node_coordinates_verified': len(source_nodes),
                         'georeferencing': '原HTML的terrainBounds，WGS84经纬度转既有局部平面',
                         'coloring': literal('summary'), 'texture_used_for_physics': False}

    def xy(self, lon, lat):
        t = self.terrain
        return t.scale_x*np.radians(lon-t.origin.lon)/1000, t.scale_y*np.radians(lat-t.origin.lat)/1000

    def texture(self, X, Y):
        """Bilinear display-only texture interpolation at DEM surface vertices."""
        xmin, xmax, ymin, ymax = self.extent
        if np.any((X < xmin) | (X > xmax) | (Y < ymin) | (Y > ymax)):
            raise ValueError('地形网格超出纹理范围')
        h, w = self.rgb.shape[:2]
        u = np.clip((X-xmin)/(xmax-xmin)*w-.5, 0, w-1)
        v = np.clip((ymax-Y)/(ymax-ymin)*h-.5, 0, h-1)
        c = np.floor(u).astype(int); r = np.floor(v).astype(int)
        c1 = np.minimum(c+1, w-1); r1 = np.minimum(r+1, h-1)
        a = (u-c)[..., None]; b = (v-r)[..., None]
        return (1-b)*((1-a)*self.rgb[r, c]+a*self.rgb[r, c1])+b*((1-a)*self.rgb[r1, c]+a*self.rgb[r1, c1])

    def boundary3d(self, ring):
        """Drape boundary on full DEM; this decorative line is not a flight path."""
        t = self.terrain
        points = []
        for a, b in zip(ring[:-1], ring[1:]):
            n = max(2, int(np.ceil(np.linalg.norm(b-a)/.025))+1)
            points.extend(np.linspace(a, b, n)[:-1])
        points.append(ring[-1]); p = np.array(points)
        lon = t.origin.lon+np.degrees(p[:, 0]*1000/t.scale_x)
        lat = t.origin.lat+np.degrees(p[:, 1]*1000/t.scale_y)
        c = np.rint((lon-t.lon0)/t.sx).astype(int)
        r = np.rint((t.lat0-lat)/t.sy).astype(int)
        if np.any((c<0)|(c>=t.cols)|(r<0)|(r>=t.rows)):
            raise ValueError('乡界超出DEM')
        z = t.elevations[r, c]
        if not np.all(np.isfinite(z)) or np.any(z == -32767):
            raise ValueError('乡界地形高程缺失')
        return p[:, 0], p[:, 1], z
