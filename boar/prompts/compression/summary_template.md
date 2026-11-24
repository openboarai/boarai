<!--
  This is a visual reference for the compressed summary output format.
  The actual rendering is done dynamically by InsertThenCompress.render_summary(),
  which skips sections whose values are empty to avoid wasting tokens.
  Keep this file in sync with the renderer logic.
-->
[BoarAi_System] Compressed History Summary

══ BACKGROUND ══
{background}

══ ARCHIVED TASK SNAPSHOT ══
Goal: {task_goal}
Step: {task_step}
Files: {task_files}

══ KEY DECISIONS ══
{decisions}

══ USER PREFERENCES ══
{preferences}
