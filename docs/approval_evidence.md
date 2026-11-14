Dear Behruz,
Thank you for the revised proposal.
Decision: Approved.
Your automated API collection, database design, statistical investigation and comparison of persistence, Ridge Regression and Random Forest provide a suitable route to LO1, LO2 and LO5. I approve the project as a small exploratory study covering the five specified locations over 21 days.
Please apply the following implementation requirements:
Fix the observation and outcome selection rules before collection. Select the exact UTC valid time required from each response; the API’s default response contains several days of forecasts. For the primary analysis, use the first accepted observation for T and the first accepted observation for exactly T+6 hours from its scheduled collection. Define a fixed allowance for delayed requests. Missed observations remain missing, and later revisions must not replace the versions used in the primary evaluation.
Preserve what was available when each prediction was made. Record the actual prediction time, inputs, model version and prediction before collecting its outcome. Match lags by timestamp rather than row position. Use common chronological boundaries across all locations, and train only on examples whose outcomes were already available before each validation or test boundary. Freeze model settings before the final seven-day test.
Treat the sample as a pilot. Retain your target of 420 scheduled snapshots and at least 300 usable paired examples, reporting the actual training, validation and test counts by location and date. Equal values at different valid times remain valid separate records; repeated retrievals and revisions do not increase the number of prediction opportunities. The seven-day test supports limited conclusions, and beating persistence is not a requirement for successful completion.
Document the statistical test precisely. Compare London and Paris using matching observation times and a predefined daily-completeness rule. Specify the Newey–West lag choice, small-sample correction and reference distribution, and examine sensitivity to the lag choice. Standard HAC implementations assume consecutive, equally spaced periods, so missing calendar days must not simply be compressed together. With at most 21 daily pairs, interpret p-values and confidence intervals cautiously.
Retain the planned queries, visualisations, robustness checks, automated tests and reproducible data snapshot. Include tests for exact six-hour outcome matching and preservation of the observation versions used for predictions.
You may proceed with assessed collection after this approval, with these rules recorded in your collection protocol. Keep any development data separate and retain this email as approval evidence. Preserve all four historical checkpoint commits for the final submission by the published LMS deadline.
Kind regards,
Sanjar Bekov

```markdown
# Module Leader Approval Record

* **Module Leader:** Sanjar Bekov (`s.bekov@udea.uz`)
* **Decision:** Approved
* **Approved Locations:** London (51.5074, -0.1278), Paris (48.8566, 2.3522), Berlin (52.5200, 13.4050), Madrid (40.4168, -3.7038), Rome (41.9028, 12.4964)
* **Approved Protocol Summary:**
  * Collect six-hourly `cams_global` estimates (00:00, 06:00, 12:00, 18:00 UTC) for 21 days (420 scheduled snapshots; target >= 300 usable pairs).
  * Enforce a 30-minute delay allowance; missed observations remain missing; preserve first accepted primary observation at `T` and `T+6h` without overwriting via later revisions.
  * Keep development/testing data separate from assessed data.