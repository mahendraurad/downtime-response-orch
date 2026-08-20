// Tasks are keyed by persona only so they work for any asset selected in the Agent Graph.
// The asset ID is injected at render time — see PersonaTasksPanel in AgentDetail.jsx.
export const PERSONA_TASKS = {
  supervisor: [
    ['Review the fault alert', 'DRO has raised this asset with a bearing fault. Open Agent Chat and ask "Analyse asset {ASSET} completely" to get the full fault summary, urgency rating, and recommended action.'],
    ['Decide: act now or defer?', 'Ask Agent Chat "What is the run/defer risk for {ASSET}?" to see the financial exposure breakdown — planned repair cost vs unplanned failure cost — before making your decision.'],
    ['Approve the work order', 'Go to Work Orders and find the pending WO for this asset. Review the checklist and confirm parts and crew are ready, then click "Approve WO" to authorise execution.'],
    ['Notify your shift team', 'Alert line operators to watch this asset closely. They should escalate immediately if vibration worsens, speed drops, or unusual noise is heard before the maintenance window.'],
    ['Include in shift handover', 'Ensure the incoming supervisor is briefed on this asset\'s status, the approved WO, and the planned maintenance window. Gaps in handover delay response.'],
  ],
  engineer: [
    ['Review the fault signature', 'Open Agent Chat and ask "What is the full fault diagnosis for {ASSET}?" to see the frequency analysis, harmonics, kurtosis, crest factor, and stage classification.'],
    ['Validate the RUL estimate', 'Ask Agent Chat "What is the RUL confidence and basis for {ASSET}?" to review the model output, confidence intervals, and load sensitivity. Confirm the estimate is credible before it drives scheduling.'],
    ['Check historical case matches', 'Ask "What historical cases match {ASSET}?" to see knowledge base precedents. Confirm the outcomes were consistent with this fault stage — this supports or challenges the recommendation.'],
    ['Define post-repair QA criteria', 'Before repair starts, specify vibration and temperature pass/fail thresholds for post-repair sign-off. Document these in the work order checklist so the maintenance crew has clear targets.'],
    ['Confirm parts and SOP fit', 'Review the bearing model and lubricant specified in the WO. Confirm the SOP revision matches current torque specs and fitting procedure for this asset class.'],
  ],
  maintenance: [
    ['Confirm all parts are staged', 'Open the Work Order for this asset and check the parts checklist — bearing, lubricant, and seal kit should all be confirmed in the staging area before the maintenance window opens.'],
    ['Assign and confirm crew', 'Confirm the assigned crew members are available, hold valid LOTO certification for this asset, and are briefed on the job scope. Tick crew confirmation in the WO checklist.'],
    ['Prepare the job pack', 'Review the SOP linked in the WO. Ensure the team has the correct torque specs, press-fit procedure, lubrication volume, and post-repair baseline target before they start.'],
    ['Book and protect the time slot', 'Confirm the maintenance window in the schedule. Notify production planning of the planned stop and block any competing jobs from the same crew during the window.'],
    ['Log actuals after completion', 'Once repair is done, close the WO and update it with actual hours and parts used. This keeps the duration model accurate for future jobs on similar assets.'],
  ],
  manager: [
    ['Review the production exposure', 'In Agent Chat, ask "What is the financial exposure if {ASSET} fails unplanned?" to get the full cost-benefit — planned repair cost vs emergency failure scenario with production loss.'],
    ['Approve the WO at operations level', 'Go to Work Orders and approve the pending WO for this asset. Planned repair cost is a fraction of the failure exposure — approving keeps logistics and crew ready to execute.'],
    ['Adjust the production plan', 'Notify production planning of the maintenance window and its duration. Confirm line throughput targets are adjusted to account for the planned downtime.'],
    ['Confirm a contingency plan', 'If the asset fails before the maintenance window: confirm the emergency crew mobilisation plan, production re-route options, and the trigger point for customer communication.'],
  ],
  executive: [
    ['Review the portfolio risk summary', 'In Agent Chat, ask "What is the financial exposure from top critical assets?" to see a risk-ranked view of the fleet. Identify the assets driving the most unplanned failure exposure this week.'],
    ['Confirm DRO ROI on this detection', 'Ask "What is the DRO ROI for {ASSET}?" — planned intervention cost vs avoided failure cost gives a concrete return figure for this event. Use for program performance reporting.'],
    ['Note for board or stakeholder reporting', 'This event demonstrates early detection before the alarm threshold, automatic WO drafting, and full logistics readiness. Document as a DRO value example in your next reporting cycle.'],
  ],
  ot: [
    ['Validate all sensor channels', 'Check all channels for this asset in the historian — vibration, temperature, current, and speed. Confirm all are logging cleanly with no recent gaps or dropout bursts.'],
    ['Confirm the fault is not a sensor artefact', 'Verify the bearing fault pattern appears across multiple consecutive scans. This confirms a real mechanical signal, not a noise event or calibration drift on a single scan.'],
    ['Set post-repair baselines in historian', 'After repair, update the historian alert thresholds to the agreed post-repair baseline values. Update the asset record in CMMS with the new bearing install date and model.'],
    ['Resolve any sensor gaps', 'If a sensor has intermittent dropouts, inspect the cable and connector at the junction box. Sensor gaps reduce RUL confidence — fix before the next scheduled analysis or inspection visit.'],
  ],
  safety: [
    ['Confirm LOTO permit is current', 'Check that the LOTO permit for this asset is valid, covers all energy isolation points, and has not expired. No crew deployment is permitted without a current, site-approved isolation permit.'],
    ['Verify ISO Zone compliance', 'Check the current vibration reading against ISO 10816-3 zone boundaries. High-zone assets require mandatory isolation before any physical approach — confirm the crew understands this applies with no exceptions.'],
    ['Check crew LOTO qualifications', 'Confirm all assigned crew members hold valid LOTO certification for this specific asset. Verify PPE availability: hearing protection and vibration-resistant gloves are required for bearing work.'],
    ['Conduct the pre-task safety brief', 'Before work starts, lead a brief covering hazard identification, isolation steps, PPE requirements, emergency stop location, and re-energisation procedure. Document attendance in the WO.'],
  ],
};
