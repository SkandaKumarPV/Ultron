"""ULTRON Super Monitor

NOTE: Some optional features (maps/spatial via geopandas) may fail to import
due to Windows permissions or library conflicts. When that happens, the feature
is disabled gracefully and can be enabled from the ULTRON Control Center GUI
(Permissions tab). All core features work regardless.
"""


def get_spatial_layer_manager():
    """Get SpatialLayerManager (lazy import - fails gracefully if geopandas blocked)."""
    try:
        from ultron.super_monitor.spatial_layers import SpatialLayerManager
        return SpatialLayerManager
    except Exception:
        class SpatialLayerManagerUnavailable:
            """Placeholder when spatial features unavailable."""
            def __init__(self, *args, **kwargs):
                raise RuntimeError(
                    "Spatial/map features unavailable (geopandas not loadable).\n"
                    "Enable in: ULTRON Control Center > Permissions > Spatial Layers"
                )
        return SpatialLayerManagerUnavailable


# Lazy loader for SpatialLayerManager itself
class _SpatialLazy:
    def __getattr__(self, name):
        mgr = get_spatial_layer_manager()
        return getattr(mgr, name)


SpatialLayerManager = _SpatialLazy()

# Core Super Monitor components (these don't depend on geopandas)
from ultron.super_monitor.monitor import SuperMonitor, SuperMonitorConfig
from ultron.super_monitor.news_aggregator import NewsAggregator
from ultron.super_monitor.weather_monitor import WeatherMonitor
from ultron.super_monitor.disaster_monitor import DisasterMonitor
from ultron.super_monitor.event_timeline import EventTimeline
from ultron.super_monitor.evidence_organizer import EvidenceOrganizer
from ultron.super_monitor.source_freshness import SourceFreshnessTracker