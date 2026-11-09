"""
ULTRON Super Monitor - Spatial Layers
Map visualization and spatial data layers using folium and geopandas.
"""

import asyncio
import logging
import base64
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from pathlib import Path
import json

import folium
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon, LineString
from shapely.ops import unary_union

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


class LayerType(Enum):
    """Types of map layers."""
    BASE = "base"
    OVERLAY = "overlay"
    HEATMAP = "heatmap"
    CLUSTER = "cluster"
    CHOROPLETH = "choropleth"
    MARKER = "marker"
    POLYGON = "polygon"
    HEAT = "heat"


@dataclass
class MapLayer:
    """A map layer configuration."""
    id: str
    name: str
    layer_type: LayerType
    visible: bool = True
    opacity: float = 1.0
    z_index: int = 0
    data: Any = None
    style: Dict = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)


class SpatialLayerManager:
    """
    Manages map layers and spatial visualizations using folium and geopandas.
    Supports multiple map projections, layer stacking, and interactive maps.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = config.resolve_path("spatial_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.layers: Dict[str, MapLayer] = {}
        self._base_maps = {
            "openstreetmap": "OpenStreetMap",
            "cartodb": "CartoDB positron",
            "cartodb_dark": "CartoDB dark_matter",
            "stamen_terrain": "Stamen Terrain",
            "stamen_toner": "Stamen Toner",
            "stamen_watercolor": "Stamen Watercolor",
            "esri_satellite": "Esri WorldImagery",
            "esri_topo": "Esri WorldTopoMap",
        }
        
        self._layers: Dict[str, MapLayer] = {}
        self._geodata_cache: Dict[str, gpd.GeoDataFrame] = {}
        self._map_cache: Dict[str, folium.Map] = {}

    def add_base_map(self, map_id: str, name: str, tiles: str, 
                     attr: str = "", max_zoom: int = 19, **kwargs) -> MapLayer:
        """Add a base map layer."""
        layer = MapLayer(
            id=map_id,
            name=name,
            layer_type=LayerType.BASE,
            metadata={
                "tiles": tiles,
                "attr": attr,
                "max_zoom": max_zoom,
                "kwargs": kwargs,
            }
        )
        self._layers[map_id] = layer
        return layer

    def add_layer(self, layer: MapLayer):
        """Add a layer."""
        self._layers[layer.id] = layer

    def remove_layer(self, layer_id: str) -> bool:
        """Remove a layer."""
        if layer_id in self._layers:
            del self._layers[layer_id]
            return True
        return False

    def get_layer(self, layer_id: str) -> Optional[MapLayer]:
        """Get layer by ID."""
        return self._layers.get(layer_id)

    def list_layers(self) -> List[MapLayer]:
        """List all layers."""
        return list(self._layers.values())

    def create_map(self, 
                   center: Tuple[float, float] = (0, 0),
                   zoom: int = 2,
                   base_map: str = "openstreetmap",
                   width: str = "100%",
                   height: str = "100%") -> folium.Map:
        """Create a base folium map."""
        
        tiles = self._base_maps.get(base_map, "OpenStreetMap")
        attr = self._get_attribution(base_map)
        
        m = folium.Map(
            location=[center[0], center[1]],
            zoom_start=zoom,
            tiles=tiles,
            attr=attr,
            width=width,
            height=height,
        )
        
        # Add all enabled layers
        for layer in sorted(self._layers.values(), key=lambda l: l.z_index):
            if layer.visible:
                self._add_layer_to_map(m, layer)
        
        return m

    def _get_attribution(self, base_map: str) -> str:
        """Get attribution for base map."""
        attributions = {
            "openstreetmap": "© OpenStreetMap contributors",
            "cartodb": "© CartoDB",
            "cartodb_dark": "© CartoDB",
            "stamen_terrain": "© Stamen Design, © OpenStreetMap contributors",
            "stamen_toner": "© Stamen Design, © OpenStreetMap contributors",
            "stamen_watercolor": "© Stamen Design, © OpenStreetMap contributors",
            "esri_satellite": "© Esri, Maxar, Earthstar Geographics",
            "esri_topo": "© Esri, HERE, Garmin, USGS, NGA, EPA, USDA, NPS",
        }
        return attributions.get(base_map, "© OpenStreetMap contributors")

    def _add_layer_to_map(self, m: folium.Map, layer: MapLayer):
        """Add a layer to the map."""
        if layer.layer_type == LayerType.MARKER:
            self._add_marker_layer(m, layer)
        elif layer.layer_type == LayerType.HEATMAP:
            self._add_heatmap_layer(m, layer)
        elif layer.layer_type == LayerType.POLYGON:
            self._add_polygon_layer(m, layer)
        elif layer.layer_type == LayerType.CHOROPLETH:
            self._add_choropleth_layer(m, layer)
        elif layer.layer_type == LayerType.CLUSTER:
            self._add_cluster_layer(m, layer)
        elif layer.layer_type == LayerType.HEAT:
            self._add_heat_layer(m, layer)

    def _add_marker_layer(self, m: folium.Map, layer: MapLayer):
        """Add marker layer."""
        data = layer.data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    lat = item.get("lat") or item.get("latitude")
                    lon = item.get("lon") or item.get("longitude")
                    if lat is not None and lon is not None:
                        popup = item.get("popup", item.get("name", ""))
                        tooltip = item.get("tooltip", item.get("title", ""))
                        icon = item.get("icon", None)
                        
                        folium.Marker(
                            location=[float(lat), float(lon)],
                            popup=folium.Popup(str(popup), max_width=300),
                            tooltip=str(tooltip),
                            icon=folium.Icon(**icon) if icon else None,
                        ).add_to(m)

    def _add_heatmap_layer(self, m: folium.Map, layer: MapLayer):
        """Add heatmap layer."""
        from folium.plugins import HeatMap
        
        data = layer.data
        if isinstance(data, list) and data:
            heat_data = []
            for item in data:
                lat = item.get("lat") or item.get("latitude")
                lon = item.get("lon") or item.get("longitude")
                weight = item.get("weight", 1.0)
                if lat is not None and lon is not None:
                    heat_data.append([float(lat), float(lon), float(weight)])
            
            if heat_data:
                HeatMap(
                    heat_data,
                    radius=layer.style.get("radius", 15),
                    blur=layer.style.get("blur", 10),
                    max_zoom=layer.style.get("max_zoom", 18),
                    min_opacity=layer.style.get("min_opacity", 0.5),
                ).add_to(m)

    def _add_heat_layer(self, m: folium.Map, layer: MapLayer):
        """Add heat layer (alias for heatmap)."""
        self._add_heatmap_layer(m, layer)

    def _add_polygon_layer(self, m: folium.Map, layer: MapLayer):
        """Add polygon layer."""
        data = layer.data
        if isinstance(data, gpd.GeoDataFrame):
            folium.GeoJson(
                data,
                style_function=lambda x: layer.style,
                tooltip=folium.GeoJsonTooltip(fields=list(data.columns)),
            ).add_to(m)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "geometry" in item:
                    folium.GeoJson(
                        item["geometry"],
                        style_function=lambda x: layer.style,
                        tooltip=item.get("tooltip"),
                    ).add_to(m)

    def _add_choropleth_layer(self, m: folium.Map, layer: MapLayer):
        """Add choropleth layer."""
        data = layer.data
        if isinstance(data, gpd.GeoDataFrame):
            folium.Choropleth(
                geo_data=data,
                data=data,
                columns=layer.style.get("columns", []),
                key_on=layer.style.get("key_on", "feature.id"),
                fill_color=layer.style.get("fill_color", "YlOrRd"),
                fill_opacity=layer.style.get("fill_opacity", 0.7),
                line_opacity=layer.style.get("line_opacity", 0.2),
                legend_name=layer.style.get("legend_name", ""),
            ).add_to(m)

    def _add_cluster_layer(self, m: folium.Map, layer: MapLayer):
        """Add marker cluster layer."""
        from folium.plugins import MarkerCluster
        
        data = layer.data
        if isinstance(data, list):
            cluster = MarkerCluster(
                name=layer.name,
                overlay=True,
                control=True,
                **layer.style.get("cluster_options", {})
            )
            
            for item in data:
                lat = item.get("lat") or item.get("latitude")
                lon = item.get("lon") or item.get("longitude")
                if lat is not None and lon is not None:
                    folium.Marker(
                        location=[float(lat), float(lon)],
                        popup=item.get("popup", ""),
                        tooltip=item.get("tooltip", ""),
                        icon=folium.Icon(**item.get("icon", {})) if item.get("icon") else None,
                    ).add_to(cluster)
            
            cluster.add_to(m)

    def generate_map(self, 
                     center: Tuple[float, float] = (0, 0),
                     zoom: int = 2,
                     base_map: str = "openstreetmap",
                     width: str = "100%",
                     height: str = "100%",
                     layers: Optional[List[str]] = None) -> str:
        """Generate map and return as HTML string."""
        
        m = self.create_map(center=center, zoom=zoom, base_map=base_map)
        
        # Filter layers if specified
        if layers:
            for layer_id in layers:
                if layer_id in self._layers:
                    self._add_layer_to_map(m, self._layers[layer_id])
        
        # Convert to HTML
        html = m._repr_html_()
        return html

    def generate_map_file(self, 
                          output_path: str,
                          center: Tuple[float, float] = (0, 0),
                          zoom: int = 2,
                          base_map: str = "openstreetmap") -> str:
        """Generate and save map as HTML file."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        html = self.generate_map(center=center, zoom=zoom, base_map=base_map)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return str(output_path)

    def add_geojson(self, 
                    layer_id: str,
                    geojson: Dict,
                    name: str,
                    style: Optional[Dict] = None,
                    layer_type: LayerType = LayerType.MARKER) -> MapLayer:
        """Add GeoJSON layer."""
        layer = MapLayer(
            id=layer_id,
            name=layer_id,
            layer_type=layer_type,
            data=geojson,
            style=style or {},
        )
        self._layers[layer_id] = layer
        return layer

    def add_geodataframe(self,
                         layer_id: str,
                         gdf: gpd.GeoDataFrame,
                         name: str,
                         style: Optional[Dict] = None,
                         layer_type: LayerType = LayerType.POLYGON) -> MapLayer:
        """Add GeoPandas GeoDataFrame as layer."""
        layer = MapLayer(
            id=layer_id,
            name=name,
            layer_type=layer_type,
            data=gdf,
            style=style or {},
        )
        self._layers[layer_id] = layer
        return layer

    def add_heatmap(self,
                    layer_id: str,
                    data: List[List[float]],  # [[lat, lon, weight], ...]
                    name: str,
                    radius: int = 15,
                    blur: int = 10,
                    max_zoom: int = 18) -> MapLayer:
        """Add heatmap layer."""
        layer = MapLayer(
            id=layer_id,
            name=name,
            layer_type=LayerType.HEATMAP,
            data=data,
            style={
                "radius": radius,
                "blur": blur,
                "max_zoom": max_zoom,
            },
        )
        self._layers[layer_id] = layer
        return layer

    def add_marker_cluster(self,
                           layer_id: str,
                           points: List[Dict],
                           name: str,
                           **cluster_options) -> MapLayer:
        """Add marker cluster layer."""
        layer = MapLayer(
            id=layer_id,
            name=name,
            layer_type=LayerType.CLUSTER,
            data=points,
            style=cluster_options,
        )
        self._layers[layer_id] = layer
        return layer

    def generate_choropleth(self,
                            layer_id: str,
                            geojson: Dict,
                            data: pd.DataFrame,
                            columns: List[str],
                            key_on: str,
                            name: str,
                            fill_color: str = "YlOrRd",
                            fill_opacity: float = 0.7,
                            line_opacity: float = 0.2,
                            legend_name: str = "") -> MapLayer:
        """Generate choropleth map layer."""
        layer = MapLayer(
            id=layer_id,
            name=name,
            layer_type=LayerType.CHOROPLETH,
            data={"geojson": geojson, "data": data},
            style={
                "columns": columns,
                "key_on": key_on,
                "fill_color": fill_color,
                "fill_opacity": fill_opacity,
                "line_opacity": line_opacity,
                "legend_name": legend_name,
            },
        )
        self._layers[layer_id] = layer
        return layer

    def export_layers_config(self) -> Dict:
        """Export layer configuration as JSON."""
        return {
            layer_id: {
                "id": layer.id,
                "name": layer.name,
                "type": layer.layer_type.value,
                "visible": layer.visible,
                "opacity": layer.opacity,
                "z_index": layer.z_index,
                "style": layer.style,
                "metadata": layer.metadata,
            }
            for layer_id, layer in self._layers.items()
        }

    def import_layers_config(self, config: Dict):
        """Import layer configuration."""
        for layer_id, layer_config in config.items():
            layer = MapLayer(
                id=layer_id,
                name=layer_config["name"],
                layer_type=LayerType(layer_config["type"]),
                visible=layer_config.get("visible", True),
                opacity=layer_config.get("opacity", 1.0),
                z_index=layer_config.get("z_index", 0),
                style=layer_config.get("style", {}),
                metadata=layer_config.get("metadata", {}),
            )
            self._layers[layer_id] = layer


# Global instance
_spatial_layer_manager: Optional[SpatialLayerManager] = None


def get_spatial_layer_manager() -> SpatialLayerManager:
    """Get or create the global Spatial Layer Manager instance."""
    global _spatial_layer_manager
    if _spatial_layer_manager is None:
        _spatial_layer_manager = SpatialLayerManager()
    return _spatial_layer_manager