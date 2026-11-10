"""Forecasting utilities for transit data."""

from .backtest_cache import (
    list_backtest_datasets,
    load_backtest_dataset,
    save_backtest_dataset,
)
from .backtest_data import BacktestDataset, download_backtest_data, get_backtest_dataset
from .congestion_display import make_congestion_map, prepare_congestion_snapshots
from .congestion_service import get_congestion_service_activity
from .delay_congestion_display import (
    make_delay_congestion_map,
    prepare_delay_congestion_snapshots,
)
from .delay_propagation import (
    compute_delay_anomalies,
    event_space_time_response,
    order_route_segments,
    same_bus_propagation,
    select_delay_events,
    summarize_propagation,
)
from .delay_propagation_display import (
    make_delay_event_plot,
    make_propagation_heatmap,
    make_time_space_diagram,
)
from .road_segment_congestion import CongestionResult, run_congestion_engine
from .road_segment_delay_congestion import (
    DelayCongestionResult,
    run_delay_congestion_engine,
)
from .static_gtfs import download_static_gtfs
from .stop_congestion_analysis import (
    StopCongestionAnalysis,
    prepare_stop_congestion_analysis,
    summarize_stop_congestion_signal,
)
from .stop_congestion_display import (
    make_stop_congestion_cohort_figures,
    make_stop_congestion_example,
)
from .trip_updates import (
    get_latest_stop_delays,
    get_stop_delay_horizons,
    get_trip_updates_for_trip,
)

__all__ = [
    "BacktestDataset",
    "CongestionResult",
    "DelayCongestionResult",
    "StopCongestionAnalysis",
    "compute_delay_anomalies",
    "download_backtest_data",
    "download_static_gtfs",
    "event_space_time_response",
    "get_backtest_dataset",
    "get_congestion_service_activity",
    "get_latest_stop_delays",
    "get_stop_delay_horizons",
    "get_trip_updates_for_trip",
    "list_backtest_datasets",
    "load_backtest_dataset",
    "make_congestion_map",
    "make_delay_congestion_map",
    "make_delay_event_plot",
    "make_propagation_heatmap",
    "make_stop_congestion_cohort_figures",
    "make_stop_congestion_example",
    "make_time_space_diagram",
    "order_route_segments",
    "prepare_congestion_snapshots",
    "prepare_delay_congestion_snapshots",
    "prepare_stop_congestion_analysis",
    "run_congestion_engine",
    "run_delay_congestion_engine",
    "same_bus_propagation",
    "save_backtest_dataset",
    "select_delay_events",
    "summarize_propagation",
    "summarize_stop_congestion_signal",
]
