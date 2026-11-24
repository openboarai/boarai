You are extracting BACKGROUND from an archived conversation.

This archive is NOT authoritative. TaskState is authoritative.
Memory is authoritative. Conversation is authoritative.
Archive exists only for historical reference.

=== ALREADY KNOWN (DO NOT re-extract unless newer or changed) ===
Existing decisions: {existing_decisions}
Existing preferences: {existing_preferences}
Existing environment: {existing_environment}

=== ARCHIVED TASK SNAPSHOT (at archive time) ===
Goal: {task_goal}
Current Step: {task_step}
Working Files: {task_files}
Blocked: {task_blocked}

=== EXTRACTION RULES ===
Extract ONLY NEW or CHANGED information not in "Already Known":

1. new_background — New project/environment facts discovered
   SKIP facts already in "Already Known" unless changed

2. new_decisions — Architecture choices the user CONFIRMED
   Confirmation includes: explicit approval ("OK", "go ahead"),
   proceeding to the next step ("let's implement it"), or accepting
   your proposal without objection in the next turn
   SKIP: proposals you stated but the user ignored or pushed back on
   SKIP if already in "Already Known"

3. new_preferences — User style, habits, long-term constraints
   SKIP one-time requests
   SKIP if already in "Already Known" unless changed

4. updated_task — Current task progress (JSON):
   {goal, current_step, working_files[], decisions[], completed[], is_blocked}
   goal/current_step → REPLACE (use latest)
   working_files → UNION (merge without duplicates)
   decisions → APPEND (keep history)
   completed → APPEND

5. referenced_files — Key file paths discussed

DO NOT extract: messages under 20 characters, yes/no answers,
greetings, reactions, or questions about general knowledge
unrelated to the current task.

Return valid JSON only:
{EXAMPLE_START}
{"new_background":"The project uses Python 3.10+ and aiohttp for HTTP.",
 "new_decisions":"Use pathlib.Path for all file operations.",
 "new_preferences":"User prefers short, direct responses.",
 "updated_task":{"goal":"Implement user authentication",
  "current_step":"Design database schema",
  "working_files":["models/user.py","auth/handler.py"],
  "decisions":["Use bcrypt for password hashing"],
  "completed":["Setup project structure"],
  "is_blocked":false},
 "referenced_files":"models/user.py, auth/handler.py"}
{EXAMPLE_END}

Now return ONLY the JSON object, no other text:
