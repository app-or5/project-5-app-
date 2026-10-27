import streamlit as st
import pandas as pd
import io
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
bp_backup = DATA_DIR / "bus_plan_empty.xlsx"


from engine import (
    load_bus_planning, load_distance_matrix, load_timetable,
    simulate_soc, run_all_feasibility_checks, compute_kpis,
    generate_bus_planning, shifted_timetable,
    Config, DEFAULT_CONFIG,
)
from visuals import build_gantt, build_soc_chart

st.set_page_config(page_title="E-Bus Planning Checker", layout="wide")

st.title("E-Bus Planning Checker")

def kleine_kpi(col, label, waarde):
    col.caption(label)
    col.markdown(f"##### {waarde}")

# set sidebar variables to adjust
with st.sidebar:
    st.header("1. Input data")
    bp_file = st.file_uploader("Bus plan (.xlsx)", type=["xlsx"], key="bp")
    dm_file = st.file_uploader("Distance matrix (.xlsx)", type=["xlsx"], key="dm")
    tt_file = st.file_uploader("Timetable (.xlsx)", type=["xlsx"], key="tt")

    st.header("2. Parameters")
    battery_capacity = st.number_input("Nameplate battery capacity (kWh)", value=300.0, step=10.0)
    soh_assumed = st.slider("Assumed State of Health (SOH)", 0.80, 1.00, 0.90, 0.01)
    safety_margin = st.slider("Safety margin SOC_min (fraction of usable capacity)", 0.0, 0.30, 0.10, 0.01)
    max_charge_fraction = st.slider("Max daily charge level (fraction of usable capacity)", 0.5, 1.0, 0.90, 0.01)
    charge_fast = st.number_input("Fast charging rate up to 90% (kW)", value=450.0, step=10.0)
    charge_slow = st.number_input("Slow charging rate 90-100% (kW)", value=60.0, step=5.0)
    min_charge_min = st.number_input("Minimum charging session c_min (minutes)", value=15.0, step=1.0)
    idle_kw = st.number_input("Idle power draw (kW)", value=5.0, step=0.5)

    config = Config(
        battery_capacity_kwh=battery_capacity,
        soh_assumed=soh_assumed,
        soc_safety_margin=safety_margin,
        soc_max_charge_fraction=max_charge_fraction,
        charge_rate_fast_kw=charge_fast,
        charge_rate_slow_kw=charge_slow,
        min_charging_minutes=min_charge_min,
        idle_power_kw=idle_kw,
    )

    st.divider()
    
    st.caption(
        f"SOC_max (usable capacity): **{config.usable_battery_capacity_kwh:.1f} kWh**  \n"
        f"SOC_min (safety margin): **{config.min_soc_kwh:.1f} kWh**  \n"
        f"Max daily charge level: **{config.max_daily_soc_kwh:.1f} kWh**"
    )



# don't do anything if the files have not been loaded yet.
if not (dm_file and tt_file):
    st.info("Upload the distance matrix and timetable in the sidebar to begin.")
    st.stop()

bp_input = bp_file if bp_file is not None else bp_backup

try:
    plan_raw = load_bus_planning(bp_input)
    dmatrix = load_distance_matrix(dm_file)
    timetable = load_timetable(tt_file)
except Exception as e:
    st.error(f"Failed to load input files: {e}")
    st.stop()

valid_locations = set(dmatrix["start"]) | set(dmatrix["end"]) | {config.depot_location}

# Run checks
plan_soc = simulate_soc(plan_raw, config)
all_issues_result = run_all_feasibility_checks(plan_soc, dmatrix, timetable, valid_locations, config)
n_errors = len(all_issues_result.errors)
n_warnings = len(all_issues_result.warnings)
kpis = compute_kpis(plan_soc, config)

# Tabs
tab_overview, tab_feasibility, tab_gantt, tab_soc, tab_kpi, tab_planner = st.tabs(
    ["Overview", "Feasibility checks", "Gantt chart", "SOC chart", "KPIs", "Planning maker"]
)

with tab_overview:
    st.subheader("Feasibility summary")
    col1, col2, col3 = st.columns(3)
    col1.metric("Overall feasible?", "No" if n_errors > 0 else "Yes")
    col2.metric("Feasibility errors", n_errors)
    col3.metric("Warnings", n_warnings)

    st.markdown(
        "This tool checks the uploaded bus plan against the 9 feasibility checks "
        "and reports the 12 KPIs defined for this project. "
        "See the Feasibility checks tab for the full list of violations, or the "
        "KPIs tab for the performance summary."
    )

    if n_errors > 0:
        st.error(
            f"The current plan is **not feasible**: {n_errors} violation(s) were found "
            "across the 9 feasibility checks. See the Feasibility checks tab for details."
        )
    else:
        st.success("All 9 feasibility checks pass - no violations found.")

with tab_feasibility:
    st.subheader("Feasibility checks")
    st.markdown(
        "Every requirement below must be met for the plan to be feasible. "
        "Each row shows which of the 9 checks was violated, on which bus, and why."
    )
    if not all_issues_result.issues:
        st.success("No issues found - all feasibility checks pass.")
    else:
        df_issues = all_issues_result.to_dataframe()
        check_filter = st.multiselect("Filter by feasibility check",
                                       sorted(df_issues["check"].unique()),
                                       default=sorted(df_issues["check"].unique()))
        sev_filter = st.multiselect("Filter by severity", ["error", "warning"],
                                     default=["error", "warning"])
        filtered = df_issues[df_issues["check"].isin(check_filter) &
                              df_issues["severity"].isin(sev_filter)]
        st.dataframe(
            filtered.rename(columns={
                "severity": "Severity", "category": "Category", "check": "Feasibility check",
                "bus": "Bus", "row": "Row", "message": "Message",
            }),
            use_container_width=True, height=500,
        )
        st.caption(f"Showing {len(filtered)} of {len(df_issues)} issues.")

        st.markdown("**Summary per feasibility check**")
        summary = df_issues.groupby("check").size().reset_index(name="Violations")
        summary = summary.rename(columns={"check": "Feasibility check"})
        st.dataframe(summary, use_container_width=True)

with tab_gantt:
    st.subheader("Bus plan - Gantt chart")
    buses_available = sorted(plan_soc["bus"].unique())
    selected_buses = st.multiselect("Filter buses (empty = show all)", buses_available)
    plot_data = plan_soc[plan_soc["bus"].isin(selected_buses)] if selected_buses else plan_soc
    fig = build_gantt(plot_data)
    st.plotly_chart(fig, use_container_width=True)

with tab_soc:
    st.subheader("State of Charge over the day")
    buses_available = sorted(plan_soc["bus"].unique())
    selected_buses_soc = st.multiselect("Filter buses (empty = show all)", buses_available,
                                         key="soc_filter")
    fig2 = build_soc_chart(plan_soc, config, bus_filter=selected_buses_soc or None)
    st.plotly_chart(fig2, use_container_width=True)

with tab_kpi:
    st.subheader("Key Performance Indicators")

    st.markdown("**Importend KPI's**")
    c1, c2, c3 = st.columns(3)
    c1.metric("1. Number of buses used", kpis["n_buses"])
    c2.metric("2. Deadhead ratio", f"{kpis['deadhead_ratio']:.2f}")
    c3.metric("3. Productive time ratio", f"{kpis['productive_time_ratio']:.1%}")
    c4, c5, c6 = st.columns(3)
    c4.metric("4. Total service hours", f"{kpis['total_service_hours']:.1f} h")
    c5.metric("5. Total idle hours", f"{kpis['total_idle_hours']:.1f} h")
    c6.metric("6. Buses breaching safety margin", kpis["buses_below_margin"])

    st.markdown("**Less importend KPI's**")
    c7, c8, c9 = st.columns(3)
    kleine_kpi(c7, "7. Number of service trips", kpis["n_service_trips"])
    kleine_kpi(c8, "8. Number of material trips", kpis["n_material_trips"])
    kleine_kpi(c9, "9. Number of charging sessions", kpis["n_charging_sessions"])
    c10, c11, c12 = st.columns(3)
    kleine_kpi(c10, "10. Total material hours", f"{kpis['total_material_hours']:.1f} h")
    kleine_kpi(c11, "11. Total charging hours", f"{kpis['total_charging_hours']:.1f} h")
    kleine_kpi(c12, "12. Lowest SOC reached", f"{kpis['min_soc_kwh_overall']:.1f} kWh")
    
    st.markdown("**KPI definitions**")
    st.markdown(
        "1. **Number of buses used** - minimize bussen used\n"
        "2. **Deadhead ratio** - material-trip hours ÷ service-trip hours (minimize)\n"
        "3. **Productive time ratio** - service-trip hours ÷ total scheduled hours (maximize)\n"
        "4. **Total service hours** - maximize service hours\n"
        "5. **Total idle hours** - minimize idle hours\n"
        "6. **Number of buses breaching the safety margin** - minimize buses breached SOC safety margin (target: 0)\n"
        "7. **Number of service trips** - maximize amount of service trips\n"
        "8. **Number of material trips** - minimize amount of material trips\n"
        "9. **Number of charging sessions** - minimize amount of charging secions\n"
        "10. **Total material hours** - minimize material hours\n"
        "11. **Total charging hours** - minimize charging hours\n"
        "12. **Lowest SOC reached** - minimize SOC across all buses and routes\n"

    )
    
with tab_planner:
    st.subheader("Create Planning")
    st.markdown(
        "Builds a bus planning from the uploaded timetable and distance matrix. "
        "Trips are handled in order of departure time. For every trip the best existing bus is chosen "
        "(already at the start stop first, then smallest waiting time, then lowest SOC); "
        "a new bus is only taken from the garage when no existing bus can do it. "
        "Buses with a low battery go to the garage at ehvbst to charge, and every bus returns to the garage at the end."
    )

    if st.button("Create planning", type="primary"):
        try:
            st.session_state["generated_plan"] = generate_bus_planning(timetable, dmatrix, config)
        except Exception as e:
            st.session_state.pop("generated_plan", None)
            st.error(f"Could not create a planning: {e}")

    generated = st.session_state.get("generated_plan")
    if generated is not None:
        gen_soc = simulate_soc(generated, config)
        gen_result = run_all_feasibility_checks(gen_soc, dmatrix, shifted_timetable(timetable),
                                                valid_locations, config)
        gen_kpis = compute_kpis(gen_soc, config)
        gen_errors = len(gen_result.errors)

        if gen_errors == 0:
            st.success("The created planning passes all feasibility checks.")
        else:
            st.error(f"The created planning has {gen_errors} feasibility violation(s).")
            st.dataframe(gen_result.to_dataframe(), use_container_width=True)

        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Buses used", gen_kpis["n_buses"])
        g2.metric("Deadhead ratio", f"{gen_kpis['deadhead_ratio']:.2f}")
        g3.metric("Productive time ratio", f"{gen_kpis['productive_time_ratio']:.1%}")
        g4.metric("Charging sessions", gen_kpis["n_charging_sessions"])

        st.plotly_chart(build_gantt(gen_soc, title="Created planning - Gantt chart"),
                        use_container_width=True)
        st.plotly_chart(build_soc_chart(gen_soc, config), use_container_width=True)

        export_columns = ["start location", "end location", "start time", "end time",
                          "activity", "line", "energy consumption", "bus"]
        export_df = generated[export_columns]
        st.dataframe(export_df, use_container_width=True, height=400)

        buffer = io.BytesIO()
        export_df.to_excel(buffer, index=False)
        st.download_button("Download planning (.xlsx)", buffer.getvalue(),
                           file_name="Generated_Bus_Planning.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.divider()