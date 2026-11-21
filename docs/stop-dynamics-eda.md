# Stop dynamics feasibility EDA

The [stop dynamics notebook](../notebooks/dynamics_model.ipynb) inspects **segment 4,
direction 0** on Route `97-KEL` using the offline `2026-11-10_2_months` cache.
In that snapshot this is Hwy 97 at Westside Rd Stn (`103745`) to Queensway
Exchange Bay B (`102853`), shape `51737`. Segment IDs are local to the selected
snapshot, route, and direction; inspect the resolved metadata after changing
the controls.

Run with the `transit_env` kernel. One interactive figure shares time across
four rows: mean instantaneous probe speed versus the engine's full-period
median probe baseline, same-trip downstream-minus-upstream arrival delay,
current-stop arrival delay, and preceding-stop arrival delay at its own reported
arrival time. Delay rows retain individual visits and five-minute medians,
including independently usable endpoint visits without a complete pair.
All rows support both-axis zoom; the legend is below the plot, separate from
the title and shared time slider.
Missing bins remain gaps. The dataset, segment, direction, bin duration, and
display window are configurable; zooming does not reload the cache or refit
the descriptive baseline.

`prepare_stop_dynamics_eda` avoids route geometry and route-wide congestion
computation, reuses validated cache loading and latest-delay selection, and
reports exclusions and daily historical matching coverage. Final reported
arrival delays remain proxies that include dwell, holding, and schedule slack.
The notebook describes a future state-space idea but performs **EDA only**:
no model formulation, parameter fitting, or forecasting.
