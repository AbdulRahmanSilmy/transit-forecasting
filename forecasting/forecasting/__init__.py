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
from .harmonic_regression import (
    CalendarHarmonics,
    HuberLassoRegressor,
    make_harmonic_time_splits,
    save_harmonic_experiment,
)
from .road_segment_congestion import (
    CongestionResult,
    build_route_segments,
    run_congestion_engine,
)
from .road_segment_delay_congestion import (
    DelayCongestionResult,
    run_delay_congestion_engine,
)
from .segment_delay_model import (
    DelayModelConfig,
    SegmentDelayData,
    make_segment_delay_splits,
    prepare_segment_delay_data,
    run_segment_delay_experiment,
    save_segment_delay_experiment,
    segment_delay_metrics,
    summarize_segment_delay_uplift,
)
from .segment_traversal_comparison import (
    EarlyTraversalComparison,
    prepare_early_traversal_comparison,
)
from .segment_traversal_comparison_display import (
    make_traversal_comparison_timeseries,
    make_traversal_prediction_scatter,
)
from .segment_traversal_speed import get_segment_traversal_speeds
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
from .stop_dynamics_display import (
    make_segment_timing_figure,
    make_stop_dynamics_identity_figure,
    make_stop_dynamics_model_figure,
)
from .stop_dynamics_eda import (
    SegmentTimingEDA,
    StopDynamicsEDA,
    prepare_segment_timing_eda,
    prepare_segment_traversal_eda,
    prepare_stop_dynamics_eda,
)
from .stop_dynamics_model import (
    StopDynamicsIdentity,
    StopDynamicsModel,
    evaluate_stop_dynamics_identity,
    fit_stop_dynamics_model,
)
from .trip_dynamics_display import make_trip_dynamics_figure
from .trip_dynamics_eda import (
    TripDynamicsEDA,
    prepare_trip_dynamics_eda,
    select_trip_dynamics_examples,
)
from .trip_updates import (
    get_latest_stop_delays,
    get_stop_delay_horizons,
    get_trip_updates_for_trip,
)

__all__ = [
    "BacktestDataset",
    "CalendarHarmonics",
    "CongestionResult",
    "DelayCongestionResult",
    "DelayModelConfig",
    "EarlyTraversalComparison",
    "HuberLassoRegressor",
    "SegmentDelayData",
    "SegmentTimingEDA",
    "StopCongestionAnalysis",
    "StopDynamicsEDA",
    "StopDynamicsModel",
    "StopDynamicsIdentity",
    "TripDynamicsEDA",
    "build_route_segments",
    "compute_delay_anomalies",
    "download_backtest_data",
    "download_static_gtfs",
    "event_space_time_response",
    "evaluate_stop_dynamics_identity",
    "fit_stop_dynamics_model",
    "get_backtest_dataset",
    "get_congestion_service_activity",
    "get_latest_stop_delays",
    "get_segment_traversal_speeds",
    "get_stop_delay_horizons",
    "get_trip_updates_for_trip",
    "list_backtest_datasets",
    "load_backtest_dataset",
    "make_congestion_map",
    "make_delay_congestion_map",
    "make_delay_event_plot",
    "make_harmonic_time_splits",
    "make_propagation_heatmap",
    "make_stop_congestion_cohort_figures",
    "make_stop_congestion_example",
    "make_segment_timing_figure",
    "make_stop_dynamics_identity_figure",
    "make_stop_dynamics_model_figure",
    "make_time_space_diagram",
    "make_trip_dynamics_figure",
    "make_traversal_comparison_timeseries",
    "make_traversal_prediction_scatter",
    "make_segment_delay_splits",
    "order_route_segments",
    "prepare_congestion_snapshots",
    "prepare_delay_congestion_snapshots",
    "prepare_early_traversal_comparison",
    "prepare_stop_congestion_analysis",
    "prepare_stop_dynamics_eda",
    "prepare_segment_timing_eda",
    "prepare_segment_traversal_eda",
    "prepare_trip_dynamics_eda",
    "prepare_segment_delay_data",
    "run_congestion_engine",
    "run_delay_congestion_engine",
    "run_segment_delay_experiment",
    "same_bus_propagation",
    "save_backtest_dataset",
    "save_harmonic_experiment",
    "save_segment_delay_experiment",
    "select_delay_events",
    "select_trip_dynamics_examples",
    "summarize_propagation",
    "summarize_stop_congestion_signal",
    "segment_delay_metrics",
    "summarize_segment_delay_uplift",
]
