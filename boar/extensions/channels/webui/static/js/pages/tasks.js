/**
 * Tasks Page Component - real backend API + schedule + edit support.
 */

// Shared default form factory: single source of truth for create/edit forms.
function defaultForm() {
  return {
    isEdit: false, editTaskId: null, saving: false,
    title: '', description: '', kind: 'ai', script_path: '',
    script_args: '', session_id: '', session_mode: 'once', prompt: '',
    model_mode: 'default', model: '',
    max_retries: 0, timeout_seconds: 0,
    schedule_type: '', schedule_run_at: '', schedule_interval: '', schedule_cron: '',
    schedule_timezone: 'UTC', execute_now: false,
    periodic_mode: 'daily',
    periodic_daily_hour: 9, periodic_daily_minute: 0,
    periodic_ndays_days: 1, periodic_ndays_hour: 0, periodic_ndays_minute: 0, periodic_ndays_second: 0,
    periodic_hourly_minute: 0,
    periodic_nhours_hours: 1, periodic_nhours_minutes: 0,
    periodic_nminutes: 30,
    periodic_weekly_weekday: 1, periodic_weekly_hour: 9, periodic_weekly_minute: 0, periodic_weekly_second: 0,
    periodic_monthly_day: 1, periodic_monthly_hour: 9, periodic_monthly_minute: 0, periodic_monthly_second: 0,
    periodic_nseconds: 60,
  };
}

const TasksPage = {
  name: 'TasksPage',
  template: `
<div class="max-w-7xl mx-auto px-4 py-6 animate-fade-in">
  <!-- Phones stack the title and the action group: side by side the title
       wrapped to two lines and the subtitle to three. -->
  <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-6">
    <div class="min-w-0">
      <h1 class="text-xl font-bold text-gray-900 dark:text-white">{{ t('tasks.title') }}</h1>
      <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ t('tasks.subtitle') }}</p>
    </div>
    <div class="flex flex-wrap items-center gap-3 sm:shrink-0">
      <button @click="loadTasks" class="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors">
        <i data-lucide="refresh-cw" class="w-4 h-4" :class="{'animate-spin':loading}"></i>
        <span>{{ t('common.refresh') }}</span>
      </button>
      <button @click="openCreateModal()" class="flex items-center gap-2 px-4 py-2.5 bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors shadow-sm shadow-primary/30">
        <i data-lucide="plus" class="w-4 h-4"></i>
        <span class="text-sm font-medium">{{ t('tasks.createTask') }}</span>
      </button>
    </div>
  </div>

  <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 mb-6">
    <div class="flex flex-col sm:flex-row gap-4">
      <div class="flex-1">
        <div class="relative">
          <i data-lucide="search" class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"></i>
          <input type="text" v-model="searchQuery" :placeholder="t('tasks.searchPlaceholder')"
            class="w-full pl-10 pr-4 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
        </div>
      </div>
      <div class="flex gap-2 flex-wrap">
        <button v-for="f in kindFilters" :key="f.value" @click="activeKindFilter=f.value"
          class="px-3 py-2 text-sm font-medium rounded-lg transition-colors"
          :class="activeKindFilter===f.value?'bg-primary text-white':'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'">{{ f.label }}</button>
      </div>
    </div>
  </div>

  <div v-if="loading" class="text-center py-12">
    <i data-lucide="loader-2" class="w-8 h-8 animate-spin text-primary mx-auto"></i>
    <p class="mt-3 text-gray-500 dark:text-gray-400">{{ t('common.loading') }}</p>
  </div>

  <div v-else-if="filteredTasks.length===0" class="text-center py-16">
    <i data-lucide="inbox" class="w-16 h-16 text-gray-300 dark:text-gray-600 mx-auto mb-4"></i>
    <p class="text-gray-500 dark:text-gray-400 text-lg">{{ t('tasks.noTasks') }}</p>
  </div>

  <div v-else class="space-y-2">
    <div v-for="task in filteredTasks" :key="task.task_id"
      class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden hover:shadow-md transition-shadow">
      <div class="px-4 py-3 flex items-center gap-3">
        <div class="flex-shrink-0">
          <div :class="task.kind==='ai'?'bg-purple-100 dark:bg-purple-900/30':'bg-cyan-100 dark:bg-cyan-900/30'" class="w-8 h-8 rounded-lg flex items-center justify-center">
            <i :data-lucide="task.kind==='ai'?'bot':'terminal'" class="w-4 h-4" :class="task.kind==='ai'?'text-purple-500':'text-cyan-500'"></i>
          </div>
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="text-sm font-medium text-gray-900 dark:text-white truncate">{{ task.title }}</span>
            <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
              :class="task.kind==='ai'?'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300':'bg-cyan-100 dark:bg-cyan-900/30 text-cyan-700 dark:text-cyan-300'">{{ task.kind==='ai'?'AI':'Script' }}</span>
            <span v-if="task.parent_task_id" class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400">
              <i data-lucide="git-branch" class="w-3 h-3 mr-1"></i>{{ t('tasks.sub') }}</span>
            <span v-if="task.schedule && task.schedule.next_run_at"
              class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">
              <i data-lucide="clock" class="w-3 h-3 mr-1"></i>{{ formatTime(task.schedule.next_run_at) }}</span>
          </div>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">{{ task.description||'-' }}</p>
        </div>
        <div v-if="task.success_count>0||task.failure_count>0" class="w-28 flex-shrink-0">
          <div class="text-xs text-center">
            <span class="inline-flex items-center gap-2 text-xs">
              <span v-if="task.success_count" class="text-green-600 dark:text-green-400 font-medium">{{ task.success_count }} {{ t('tasks.successCount') }}</span>
              <span v-if="task.failure_count" class="text-red-500 dark:text-red-400 font-medium">{{ task.failure_count }} {{ t('tasks.failureCount') }}</span>
            </span>
          </div>
        </div>
        <div class="flex items-center gap-1">
          <button v-if="canExecute(task)" @click.stop="executeTask(task)" :disabled="isTaskBusy(task)" :title="t('tasks.run')"
            class="p-2 rounded-lg hover:bg-green-100 dark:hover:bg-green-900/30 text-green-600 dark:text-green-400 disabled:opacity-40 disabled:cursor-not-allowed"><i data-lucide="play" class="w-4 h-4"></i></button>
          <button v-if="canCancel(task)" @click.stop="cancelTask(task)" :disabled="isTaskBusy(task)" :title="t('tasks.cancel')"
            class="p-2 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/30 text-red-500 dark:text-red-400 disabled:opacity-40 disabled:cursor-not-allowed"><i data-lucide="square" class="w-4 h-4"></i></button>
          <button v-if="canPause(task)" @click.stop="pauseTask(task)" :disabled="isTaskBusy(task)" :title="t('tasks.pause')"
            class="p-2 rounded-lg hover:bg-amber-100 dark:hover:bg-amber-900/30 text-amber-500 disabled:opacity-40 disabled:cursor-not-allowed"><i data-lucide="pause" class="w-4 h-4"></i></button>
          <button v-if="canResume(task)" @click.stop="resumeTask(task)" :disabled="isTaskBusy(task)" :title="t('tasks.resume')"
            class="p-2 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 text-blue-500 disabled:opacity-40 disabled:cursor-not-allowed"><i data-lucide="play" class="w-4 h-4"></i></button>
          <button @click.stop="openEditModal(task)" :title="t('common.edit')"
            class="p-2 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 text-blue-500"><i data-lucide="pencil" class="w-4 h-4"></i></button>
          <button @click.stop="toggleDetail(task)"
            class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400"><i data-lucide="chevron-down" class="w-4 h-4" :class="{'rotate-180':detailTaskId===task.task_id}"></i></button>
          <button @click.stop="confirmDelete(task)" :disabled="isTaskBusy(task)"
            class="p-2 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-500 disabled:opacity-40 disabled:cursor-not-allowed"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        </div>
      </div>

      <div v-if="detailTaskId===task.task_id" class="border-t border-gray-200 dark:border-gray-700 px-4 py-3 bg-gray-50 dark:bg-gray-750 text-sm space-y-2">
        <div class="grid grid-cols-2 gap-x-6 gap-y-1">
          <div><span class="text-gray-500">{{ t('tasks.taskId') }}:</span><span class="ml-2 text-gray-700 dark:text-gray-300 font-mono text-xs">{{ task.task_id }}</span></div>
          <div><span class="text-gray-500">{{ t('tasks.kind') }}:</span><span class="ml-2 text-gray-700 dark:text-gray-300">{{ task.kind }}</span></div>
          <div><span class="text-gray-500">{{ t('tasks.subTasks') }}:</span><span class="ml-2 text-gray-700 dark:text-gray-300">{{ task.sub_tasks?task.sub_tasks.length:0 }}</span></div>
          <div><span class="text-gray-500">{{ t('tasks.steps') }}:</span><span class="ml-2 text-gray-700 dark:text-gray-300">{{ task.steps?task.steps.length:0 }}</span></div>
          <div><span class="text-gray-500">{{ t('tasks.maxRetries') }}:</span><span class="ml-2 text-gray-700 dark:text-gray-300">{{ task.max_retries }}</span></div>
          <div v-if="task.started_at"><span class="text-gray-500">{{ t('tasks.startedAt') }}:</span><span class="ml-2 text-gray-700 dark:text-gray-300">{{ formatTime(task.started_at) }}</span></div>
          <div v-if="task.completed_at"><span class="text-gray-500">{{ t('tasks.completedAt') }}:</span><span class="ml-2 text-gray-700 dark:text-gray-300">{{ formatTime(task.completed_at) }}</span></div>
          <div v-if="task.schedule"><span class="text-gray-500">{{ t('tasks.schedule') }}:</span><span class="ml-2 text-gray-700 dark:text-gray-300">{{ task.schedule.type }} {{ task.schedule.next_run_at?formatTime(task.schedule.next_run_at):'' }}</span></div>
        </div>
        <div v-if="task.error" class="mt-2 p-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
          <p class="text-xs text-red-600 dark:text-red-400">{{ task.error }}</p>
        </div>
          <div v-if="task.result&&task.result.output" class="mt-2">
          <p class="text-xs font-medium text-gray-500 mb-1">{{ t('tasks.resultOutput') }}:</p>
          <pre class="text-xs text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 p-2 rounded-lg border border-gray-200 dark:border-gray-700 max-h-32 overflow-y-auto">{{ task.result.output }}</pre>
        </div>
        <div v-if="task.result&&task.result.output_files&&task.result.output_files.length>0" class="mt-2">
          <p class="text-xs font-medium text-gray-500 mb-1">{{ t('tasks.outputFiles') }}:</p>
          <div v-for="f in task.result.output_files" :key="f" class="text-xs text-blue-600 font-mono">{{ f }}</div>
        </div>
        <div v-if="task.run_history&&task.run_history.length>0" class="mt-2">
          <p class="text-xs font-medium text-gray-500 mb-1">{{ t('tasks.runHistory') || 'Run History' }} ({{ task.run_history.length }}):</p>
          <div v-for="(r,i) in task.run_history.slice(-8)" :key="i" class="text-xs py-1 flex items-center gap-2 text-gray-600 dark:text-gray-400">
            <i :data-lucide="r.success?'check-circle':'alert-circle'" class="w-3 h-3" :class="r.success?'text-green-500':'text-red-500'"></i>
            <span class="font-mono">{{ formatTime(r.completed_at) }}</span>
            <span class="px-1 py-0.5 rounded text-xs" :class="r.status==='completed'?'bg-green-100 text-green-700':'bg-red-100 text-red-700'">{{ r.status }}</span>
            <span v-if="r.error" class="truncate max-w-[140px] text-red-500">: {{ r.error }}</span>
            <span v-else-if="r.output_preview" class="truncate max-w-[140px] text-gray-400">: {{ r.output_preview }}</span>
          </div>
        </div>
        <div v-if="task.sub_tasks&&task.sub_tasks.length>0" class="mt-2">
          <p class="text-xs font-medium text-gray-500 mb-1">{{ t('tasks.subTasks') }}:</p>
          <div v-for="sub in task.sub_tasks" :key="sub.task_id" class="flex items-center gap-2 py-1 pl-4 border-l-2 border-gray-300 dark:border-gray-600 ml-2">
            <i :data-lucide="getStatusIcon(sub.status)" class="w-3 h-3" :class="getStatusIconColor(sub.status)"></i>
            <span class="text-xs text-gray-700 dark:text-gray-300">{{ sub.title }}</span>
            <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium" :class="getStatusBadge(sub.status)">{{ getStatusLabel(sub.status) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <base-modal :model-value="showModal" :title="formData.isEdit?t('common.edit'):t('tasks.createNewTask')"
              width-class="max-w-[700px]" body-class="p-0"
              @update:model-value="closeModal()" @close="closeModal()">
      <!-- Body: scrollable -->
      <form @submit.prevent="saveTask()" class="px-6 py-4 overflow-y-auto flex-1 space-y-4">

        <!-- Task Type — first, compact -->
        <div class="flex items-center gap-4">
          <span class="text-sm font-medium text-gray-700 dark:text-gray-300 whitespace-nowrap">{{ t('tasks.taskType') }} *</span>
          <segmented-control :model-value="formData.kind" :options="taskKindOptions"
                             @update:model-value="formData.kind = $event"></segmented-control>
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.taskTitle') }} *</label>
          <input type="text" v-model="formData.title" required
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.description') }}</label>
          <textarea v-model="formData.description" rows="3"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"></textarea>
        </div>

        <!-- Kind-specific fields -->
        <div v-if="formData.kind==='ai'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.prompt') }} *</label>
          <textarea v-model="formData.prompt" rows="6" required :placeholder="t('tasks.promptPlaceholder')"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"></textarea>
          <p class="text-xs text-gray-400 mt-1">{{ t('tasks.characters', {count: formData.prompt?.length||0}) }}</p>
        </div>
        <div v-if="formData.kind==='ai'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('tasks.model') }}</label>
          <div class="mb-3">
            <segmented-control :model-value="formData.model_mode" :options="modelModeOptions"
                               @update:model-value="setModelMode($event)"></segmented-control>
          </div>
          <div v-if="formData.model_mode==='specify'" class="relative">
            <select v-model="formData.model"
              class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary appearance-none pr-8">
              <option value="">{{ t('tasks.selectModel') }}</option>
              <option v-if="availableModels.length===0 && loadingModels" disabled>{{ t('common.loading') || 'Loading...' }}</option>
              <option v-for="m in availableModels" :key="m.id || m.name" :value="m.id || m.name">
                {{ m.name || m.id }}
              </option>
            </select>
            <i data-lucide="chevron-down" class="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-gray-400"></i>
          </div>
          <p class="text-xs text-gray-400 mt-1">
            <template v-if="formData.model_mode==='default'">{{ t('tasks.modelDefaultHint') }}</template>
          </p>
        </div>
        <div v-if="formData.kind==='script'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.scriptPath') }} *</label>
          <input type="text" v-model="formData.script_path" required :placeholder="t('tasks.scriptPathPlaceholder')"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
          <p class="text-xs text-gray-400 mt-1">{{ t('tasks.scriptDirHint') }}</p>
        </div>
        <div v-if="formData.kind==='script'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.scriptArgs') }}</label>
          <input type="text" v-model="formData.script_args" :placeholder="t('tasks.scriptArgsPlaceholder')"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
        </div>

        <div v-if="formData.kind==='ai'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('tasks.sessionMode') || 'Session Mode' }}</label>
          <div class="mb-3">
            <segmented-control :model-value="formData.session_mode" :options="sessionModeOptions"
                               @update:model-value="setSessionMode($event)"></segmented-control>
          </div>
          <div v-if="formData.session_mode==='manual'" class="relative">
            <select v-model="formData.session_id"
              class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary appearance-none pr-8">
              <option value="">{{ t('tasks.selectSession') || '-- Select session --' }}</option>
              <option v-if="loadingSessions" disabled>{{ t('common.loading') }}...</option>
              <option v-for="sess in sessions" :key="sess.session_id" :value="sess.session_id">
                {{ sess.title || sess.session_id.slice(0,8) }} · {{ formatSessionTime(sess.updated_at) }}
              </option>
            </select>
            <i data-lucide="chevron-down" class="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-gray-400"></i>
          </div>
          <p class="text-xs text-gray-400 mt-1">
            <template v-if="formData.session_mode==='once'">{{ t('tasks.sessionModeOnceHint') || 'Auto-create on first run, then reuse' }}</template>
            <template v-else-if="formData.session_mode==='always'">{{ t('tasks.sessionModeAlwaysHint') || 'Create a new session every run' }}</template>
            <template v-else>{{ t('tasks.sessionModeManualHint') || 'Use the selected session' }}</template>
          </p>
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.maxRetries') }}</label>
            <input type="number" min="0" max="10" v-model.number="formData.max_retries"
              class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
            <p class="text-xs text-gray-400 mt-1">{{ t('tasks.retryHint') }}</p>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.timeoutSeconds') }}</label>
            <input type="number" min="0" v-model.number="formData.timeout_seconds" :placeholder="t('tasks.timeoutPlaceholder')"
              class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
            <p class="text-xs text-gray-400 mt-1">{{ t('tasks.timeoutHint') }}</p>
          </div>
        </div>

        <!-- Schedule -->
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('tasks.scheduleType') }}</label>
          <div class="grid grid-cols-4 gap-3">
            <button v-for="opt in scheduleTypeOptions" :key="opt.value" type="button"
                    @click="formData.schedule_type=opt.value"
                    :class="formData.schedule_type===opt.value?'border-primary bg-primary/5':'border-gray-200 dark:border-gray-600'"
                    class="p-3 border-2 rounded-lg text-center transition-colors hover:border-primary/50">
              <i :data-lucide="opt.icon" class="w-5 h-5 mx-auto mb-1" :class="opt.color"></i>
              <div class="text-xs font-medium">{{ opt.label }}</div>
            </button>
          </div>
        </div>

        <div v-if="formData.schedule_type==='once'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.runAt') }} *</label>
          <input type="datetime-local" v-model="formData.schedule_run_at" :min="getMinDateTime()"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
        </div>
        
        <div v-if="formData.schedule_type==='periodic'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('tasks.periodicMode') || 'Periodic Mode' }} *</label>
          <select v-model="formData.periodic_mode"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary mb-3">
            <option value="daily">{{ t('tasks.periodicDaily') || '每天 (Daily)' }}</option>
            <option value="ndays">{{ t('tasks.periodicNDays') || 'N天 (Every N Days)' }}</option>
            <option value="hourly">{{ t('tasks.periodicHourly') || '每小时 (Hourly)' }}</option>
            <option value="nhours">{{ t('tasks.periodicNHours') || 'N小时 (Every N Hours)' }}</option>
            <option value="nminutes">{{ t('tasks.periodicNMinutes') || 'N分钟 (Every N Minutes)' }}</option>
            <option value="weekly">{{ t('tasks.periodicWeekly') || '每周 (Weekly)' }}</option>
            <option value="monthly">{{ t('tasks.periodicMonthly') || '每月 (Monthly)' }}</option>
            <option value="nseconds">{{ t('tasks.periodicNSeconds') || 'N秒 (Every N Seconds)' }}</option>
          </select>

          <!-- Daily: hour:minute -->
          <div v-if="formData.periodic_mode==='daily'" class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.hour') || 'Hour' }}</label>
              <input type="number" v-model.number="formData.periodic_daily_hour" min="0" max="23"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.minute') || 'Minute' }}</label>
              <input type="number" v-model.number="formData.periodic_daily_minute" min="0" max="59"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
          </div>

          <!-- N Days: days, hour, minute, second -->
          <div v-if="formData.periodic_mode==='ndays'" class="grid grid-cols-4 gap-3">
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.days') || 'Days' }}</label>
              <input type="number" v-model.number="formData.periodic_ndays_days" min="1"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.hour') || 'Hour' }}</label>
              <input type="number" v-model.number="formData.periodic_ndays_hour" min="0" max="23"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.minute') || 'Min' }}</label>
              <input type="number" v-model.number="formData.periodic_ndays_minute" min="0" max="59"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.second') || 'Sec' }}</label>
              <input type="number" v-model.number="formData.periodic_ndays_second" min="0" max="59"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
          </div>

          <!-- Hourly: minute -->
          <div v-if="formData.periodic_mode==='hourly'">
            <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.minute') || 'Minute of Hour' }}</label>
            <input type="number" v-model.number="formData.periodic_hourly_minute" min="0" max="59"
              class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
          </div>

          <!-- N Hours: hours, minutes -->
          <div v-if="formData.periodic_mode==='nhours'" class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.hours') || 'Hours' }}</label>
              <input type="number" v-model.number="formData.periodic_nhours_hours" min="1"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.minutes') || 'Minutes' }}</label>
              <input type="number" v-model.number="formData.periodic_nhours_minutes" min="0" max="59"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
          </div>

          <!-- N Minutes -->
          <div v-if="formData.periodic_mode==='nminutes'">
            <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.minutes') || 'Minutes' }}</label>
            <input type="number" v-model.number="formData.periodic_nminutes" min="1"
              class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
          </div>

          <!-- Weekly: weekday, hour, minute, second -->
          <div v-if="formData.periodic_mode==='weekly'" class="grid grid-cols-4 gap-3">
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.weekday') || 'Weekday' }}</label>
              <select v-model.number="formData.periodic_weekly_weekday"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
                <option :value="1">{{ t('tasks.monday') || 'Mon' }}</option>
                <option :value="2">{{ t('tasks.tuesday') || 'Tue' }}</option>
                <option :value="3">{{ t('tasks.wednesday') || 'Wed' }}</option>
                <option :value="4">{{ t('tasks.thursday') || 'Thu' }}</option>
                <option :value="5">{{ t('tasks.friday') || 'Fri' }}</option>
                <option :value="6">{{ t('tasks.saturday') || 'Sat' }}</option>
                <option :value="0">{{ t('tasks.sunday') || 'Sun' }}</option>
              </select>
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.hour') || 'Hour' }}</label>
              <input type="number" v-model.number="formData.periodic_weekly_hour" min="0" max="23"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.minute') || 'Min' }}</label>
              <input type="number" v-model.number="formData.periodic_weekly_minute" min="0" max="59"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.second') || 'Sec' }}</label>
              <input type="number" v-model.number="formData.periodic_weekly_second" min="0" max="59"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
          </div>

          <!-- Monthly: day, hour, minute, second -->
          <div v-if="formData.periodic_mode==='monthly'" class="grid grid-cols-4 gap-3">
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.dayOfMonth') || 'Day' }}</label>
              <input type="number" v-model.number="formData.periodic_monthly_day" min="1" max="31"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.hour') || 'Hour' }}</label>
              <input type="number" v-model.number="formData.periodic_monthly_hour" min="0" max="23"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.minute') || 'Min' }}</label>
              <input type="number" v-model.number="formData.periodic_monthly_minute" min="0" max="59"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
            <div>
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.second') || 'Sec' }}</label>
              <input type="number" v-model.number="formData.periodic_monthly_second" min="0" max="59"
                class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
            </div>
          </div>

          <!-- N Seconds -->
          <div v-if="formData.periodic_mode==='nseconds'">
            <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ t('tasks.seconds') || 'Seconds' }}</label>
            <input type="number" v-model.number="formData.periodic_nseconds" min="1"
              class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg">
          </div>
        </div>

        <div v-if="formData.schedule_type==='interval'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.scheduleInterval') }} *</label>
          <input type="text" v-model="formData.schedule_interval" :placeholder="t('tasks.intervalPlaceholder')" @input="validateIntervalInput"
            :class="intervalValid===false?'border-red-500 focus:ring-red-500':''"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
          <p class="text-xs mt-1" :class="intervalValid===false?'text-red-500':'text-gray-400'">{{ t('tasks.intervalExamples') }}</p>
        </div>
        <div v-if="formData.schedule_type==='cron'">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.cronLabel') }} *</label>
          <input type="text" v-model="formData.schedule_cron" :placeholder="t('tasks.cronPlaceholder')" @input="validateCronInput"
            :class="cronValid===false?'border-red-500 focus:ring-red-500':''"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
          <p class="text-xs text-gray-400 mt-1">{{ t('tasks.cronHint') }}</p>
          <div class="mt-3 p-3 bg-gray-50 dark:bg-gray-750 rounded-lg">
            <div class="text-xs font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('tasks.cronExamples') }}</div>
            <div class="space-y-1">
              <div v-for="(ex,i) in cronExamples" :key="i" class="flex items-center justify-between text-xs">
                <span class="text-gray-600 dark:text-gray-400">{{ ex.label }}</span>
                <button type="button" @click="formData.schedule_cron=ex.value"
                  class="px-2 py-1 text-primary hover:bg-primary/10 rounded">{{ t('tasks.useExample') }}</button>
              </div>
            </div>
          </div>
        </div>
        <div v-if="formData.schedule_type">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('tasks.timezone') }}</label>
          <select v-model="formData.schedule_timezone"
            class="w-full px-3 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
            <option value="UTC">UTC (GMT+0)</option>
            <option value="Asia/Shanghai">Asia/Shanghai (GMT+8)</option>
            <option value="Asia/Tokyo">Asia/Tokyo (GMT+9)</option>
            <option value="Europe/London">Europe/London (GMT+0/+1)</option>
            <option value="America/New_York">America/New_York (EST/EDT)</option>
            <option value="America/Los_Angeles">America/Los_Angeles (PST/PDT)</option>
          </select>
        </div>
        <div v-if="formData.schedule_type && schedulePreview" class="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
          <div class="text-xs font-medium text-blue-700 dark:text-blue-300 mb-1">{{ t('tasks.schedulePreview') }}</div>
          <div class="text-sm text-blue-600 dark:text-blue-400">{{ schedulePreview }}</div>
        </div>

      </form>

      <!-- Footer: fixed -->
      <template #footer>
        <div class="flex justify-end gap-3">
          <base-button variant="secondary" @click="closeModal()">{{ t('common.cancel') }}</base-button>
          <base-button variant="primary" :loading="formData.saving" :disabled="!canSubmit" @click="saveTask()">
            {{ t('common.save') }}
          </base-button>
        </div>
      </template>
    </base-modal>
  </div>
</div>
  `,

  data() {
    return {
      loading: false,
      showModal: false,
      searchQuery: '',
      activeKindFilter: 'all',
      detailTaskId: null,
      tasks: [],
      sessions: [],
      loadingSessions: false,
      intervalValid: null,
      cronValid: null,
      availableModels: [],
      loadingModels: false,
      formData: defaultForm(),
      busyTaskIds: {},
      kindFilters: [],
      cronExamples: [],
    };
  },

  computed: {
    taskKindOptions() {
      return [
        { value: 'ai', label: 'AI', icon: 'bot' },
        { value: 'script', label: 'Script', icon: 'terminal' },
      ];
    },
    modelModeOptions() {
      return [
        { value: 'default', label: this.t('tasks.modelDefault') },
        { value: 'specify', label: this.t('tasks.modelSpecify') },
      ];
    },
    sessionModeOptions() {
      return [
        { value: 'once', label: this.t('tasks.sessionModeOnce') || 'Bind Once' },
        { value: 'always', label: this.t('tasks.sessionModeAlways') || 'Always New' },
        { value: 'manual', label: this.t('tasks.sessionModeManual') || 'Select' },
      ];
    },
    scheduleTypeOptions() {
      return [
        { value: '', label: this.t('tasks.noSchedule'), icon: 'ban', color: 'text-gray-500' },
        { value: 'once', label: this.t('tasks.scheduleOnce'), icon: 'clock', color: 'text-amber-500' },
        { value: 'periodic', label: this.t('tasks.schedulePeriodic') || 'Periodic', icon: 'repeat', color: 'text-blue-500' },
        { value: 'cron', label: this.t('tasks.scheduleCron'), icon: 'calendar', color: 'text-green-500' },
      ];
    },
    filteredTasks() {
      let f = this.tasks;
      if (this.activeKindFilter!=='all') f = f.filter(t=>t.kind===this.activeKindFilter);
      if (this.searchQuery) {
        const q = this.searchQuery.toLowerCase();
        f = f.filter(t=>(t.title||'').toLowerCase().includes(q)||(t.description||'').toLowerCase().includes(q));
      }
      return f;
    },
    canSubmit() {
      if (!this.formData.title?.trim()) return false;
      if (this.formData.kind === 'ai' && !this.formData.prompt?.trim()) return false;
      if (this.formData.kind === 'script' && !this.formData.script_path?.trim()) return false;
      if (this.formData.schedule_type === 'once' && !this.formData.schedule_run_at) return false;
      if (this.formData.schedule_type === 'periodic' && !this.formData.periodic_mode) return false;
      if (this.formData.schedule_type === 'interval' && !this.validateInterval(this.formData.schedule_interval)) return false;
      if (this.formData.schedule_type === 'cron' && !this.validateCron(this.formData.schedule_cron)) return false;
      return true;
    },
    schedulePreview() {
      const t = this.formData.schedule_type;
      const fmt = (v) => String(v).padStart(2, '0');
      if (t === 'once' && this.formData.schedule_run_at) {
        return `${this.t('tasks.willRunAt')}: ${new Date(this.formData.schedule_run_at).toLocaleString()}`;
      }
      if (t === 'periodic') {
        const mode = this.formData.periodic_mode;
        if (mode === 'daily') {
          return `${this.t('tasks.periodicDaily')}: ${fmt(this.formData.periodic_daily_hour)}:${fmt(this.formData.periodic_daily_minute)}`;
        }
        if (mode === 'ndays') {
          return this.t('tasks.previewEveryNDays', {
            d: this.formData.periodic_ndays_days,
            h: this.formData.periodic_ndays_hour,
            m: this.formData.periodic_ndays_minute,
            s: this.formData.periodic_ndays_second,
          });
        }
        if (mode === 'hourly') {
          return `${this.t('tasks.periodicHourly')}: ${this.t('tasks.previewMinute', { value: this.formData.periodic_hourly_minute })}`;
        }
        if (mode === 'nhours') {
          return this.t('tasks.previewEveryNHours', {
            h: this.formData.periodic_nhours_hours,
            m: this.formData.periodic_nhours_minutes,
          });
        }
        if (mode === 'nminutes') {
          return this.t('tasks.previewEveryNMinutes', { value: this.formData.periodic_nminutes });
        }
        if (mode === 'weekly') {
          const dayKey = ['sunday','monday','tuesday','wednesday','thursday','friday','saturday'][this.formData.periodic_weekly_weekday] || 'sunday';
          return `${this.t('tasks.periodicWeekly')}: ${this.t('tasks.weekday' + dayKey.charAt(0).toUpperCase() + dayKey.slice(1))} ${fmt(this.formData.periodic_weekly_hour)}:${fmt(this.formData.periodic_weekly_minute)}:${fmt(this.formData.periodic_weekly_second)}`;
        }
        if (mode === 'monthly') {
          return `${this.t('tasks.periodicMonthly')}: ${this.t('tasks.previewDay', { value: this.formData.periodic_monthly_day })} ${fmt(this.formData.periodic_monthly_hour)}:${fmt(this.formData.periodic_monthly_minute)}:${fmt(this.formData.periodic_monthly_second)}`;
        }
        if (mode === 'nseconds') {
          return this.t('tasks.previewEveryNSeconds', { value: this.formData.periodic_nseconds });
        }
      }
      if (t === 'interval' && this.formData.schedule_interval) {
        return `${this.t('tasks.scheduleInterval')}: ${this.formData.schedule_interval}`;
      }
      if (t === 'cron' && this.formData.schedule_cron) {
        return this.t('tasks.previewCron', { expr: this.formData.schedule_cron });
      }
      return null;
    }
  },

  mounted() {
    this.initFilters();
    this.initCronExamples();
    this.loadTasks();
    // Listen for real-time task events via WebSocket (debounced)
    this._onTaskEventStore = (eventType, payload) => {
      if (this._isDestroyed) return;
      clearTimeout(this._taskRefreshTimer);
      this._taskRefreshTimer = setTimeout(() => {
        if (this._isDestroyed) return;
        this.loadTasks();
      }, 1000);
    };
    BoarAiStore._onTaskEvent = this._onTaskEventStore;
  },
  // Icons hydrate automatically via app.js MutationObserver (childList + attr).
  updated() {},
  beforeUnmount() {
    this._isDestroyed = true;
    if (this._taskRefreshTimer) clearTimeout(this._taskRefreshTimer);
    if (BoarAiStore._onTaskEvent === this._onTaskEventStore) {
      BoarAiStore._onTaskEvent = null;
    }
  },

  methods: {
    // handleLanguageChange overrides global I18nMixin: also re-init filters.
    handleLanguageChange() {
      this.currentLanguage = window.i18n ? window.i18n.getCurrentLanguage() : 'en';
      this.initFilters();
      this.initCronExamples();
    },

    initFilters() {
      this.kindFilters = [
        {value:'all',label:this.t('tasks.all')},
        {value:'ai',label:this.t('tasks.aiTask')},
        {value:'script',label:this.t('tasks.scriptTask')},
      ];
    },

    initCronExamples() {
      this.cronExamples = [
        { label: this.t('tasks.cronExample1'), value: '0 9 * * *' },
        { label: this.t('tasks.cronExample2'), value: '0 18 * * 1-5' },
        { label: this.t('tasks.cronExample3'), value: '*/5 * * * *' },
        { label: this.t('tasks.cronExample4'), value: '0 9 * * 1' },
      ];
    },

    getMinDateTime() {
      const d = new Date();
      d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
      return d.toISOString().slice(0, 16);
    },

    validateInterval(expr) {
      if (!expr || !String(expr).trim()) return false;
      // Align with backend scheduler: 30m / 2h / 7d (no seconds)
      return /^\d+\s*(m|min|h|hr|d|day)s?$/i.test(String(expr).trim());
    },

    validateCron(expr) {
      if (!expr || !String(expr).trim()) return false;
      const parts = String(expr).trim().split(/\s+/);
      return parts.length === 5 || parts.length === 6;
    },

    validateIntervalInput() {
      const v = this.formData.schedule_interval;
      this.intervalValid = !v ? null : this.validateInterval(v);
    },

    validateCronInput() {
      const v = this.formData.schedule_cron;
      this.cronValid = !v ? null : this.validateCron(v);
    },

    buildSchedulePayload() {
      const tz = this.formData.schedule_timezone || 'UTC';
      if (this.formData.schedule_type === 'once' && this.formData.schedule_run_at) {
        return {
          type: 'once',
          next_run_at: new Date(this.formData.schedule_run_at).toISOString(),
          timezone: tz,
        };
      }
      if (this.formData.schedule_type === 'periodic') {
        const mode = this.formData.periodic_mode;
        let expr = '';
        if (mode === 'daily') {
          expr = `1d@${this.formData.periodic_daily_hour}:${this.formData.periodic_daily_minute}`;
        } else if (mode === 'ndays') {
          const d = this.formData.periodic_ndays_days;
          const h = this.formData.periodic_ndays_hour;
          const m = this.formData.periodic_ndays_minute;
          const s = this.formData.periodic_ndays_second;
          const totalSeconds = d * 86400 + h * 3600 + m * 60 + s;
          expr = `${totalSeconds}s`;
        } else if (mode === 'hourly') {
          expr = `1h@${this.formData.periodic_hourly_minute}`;
        } else if (mode === 'nhours') {
          const totalMinutes = this.formData.periodic_nhours_hours * 60 + this.formData.periodic_nhours_minutes;
          expr = `${totalMinutes}m`;
        } else if (mode === 'nminutes') {
          expr = `${this.formData.periodic_nminutes}m`;
        } else if (mode === 'weekly') {
          expr = `1w@${this.formData.periodic_weekly_weekday}:${this.formData.periodic_weekly_hour}:${this.formData.periodic_weekly_minute}:${this.formData.periodic_weekly_second}`;
        } else if (mode === 'monthly') {
          expr = `1M@${this.formData.periodic_monthly_day}:${this.formData.periodic_monthly_hour}:${this.formData.periodic_monthly_minute}:${this.formData.periodic_monthly_second}`;
        } else if (mode === 'nseconds') {
          expr = `${this.formData.periodic_nseconds}s`;
        }
        return {
          type: 'interval',
          expression: expr,
          timezone: tz,
        };
      }
      if (this.formData.schedule_type === 'interval' && this.formData.schedule_interval) {
        return {
          type: 'interval',
          expression: this.formData.schedule_interval.trim(),
          timezone: tz,
        };
      }
      if (this.formData.schedule_type === 'cron' && this.formData.schedule_cron) {
        return {
          type: 'cron',
          expression: this.formData.schedule_cron.trim(),
          timezone: tz,
        };
      }
      return null;
    },

    resetForm() {
      this.intervalValid = null;
      this.cronValid = null;
      this.formData = defaultForm();
    },

    async openCreateModal() {
      this.resetForm();
      this.showModal = true;
      this.loadModelsForPicker();
    },

    async loadModelsForPicker() {
      if (this.availableModels.length > 0) return;
      this.loadingModels = true;
      try {
        const data = await BoarAiStore.api.getModels();
        this.availableModels = data.models || [];
      } catch (e) {
        console.warn('Failed to load models for task picker:', e);
        this.availableModels = [];
      } finally {
        this.loadingModels = false;
      }
    },

    async openEditModal(task) {
      let full = task;
      try {
        full = await BoarAiStore.api.getTask(task.task_id) || task;
      } catch (e) { /* use list row */ }

      const s = full.schedule || {};
      let runAt = '';
      if (s.next_run_at) {
        try {
          const d = new Date(s.next_run_at);
          if (!Number.isNaN(d.getTime())) {
            d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
            runAt = d.toISOString().slice(0, 16);
          }
        } catch (e) {
          runAt = String(s.next_run_at).slice(0, 16);
        }
      }

      this.formData = Object.assign(defaultForm(), {
        isEdit: true,
        editTaskId: full.task_id,
        title: full.title || '',
        description: full.description || '',
        kind: full.kind || 'ai',
        script_path: full.script_path || '',
        script_args: (full.script_args || []).join(' '),
        session_id: full.session_id || '',
        session_mode: full.session_mode || 'once',
        prompt: full.prompt || '',
        model_mode: full.model ? 'specify' : 'default',
        model: full.model || '',
        max_retries: full.max_retries || 0,
        timeout_seconds: full.timeout_seconds || 0,
        schedule_type: s.type || '',
        schedule_run_at: runAt,
        schedule_interval: s.type === 'interval' ? (s.expression || '') : '',
        schedule_cron: s.type === 'cron' ? (s.expression || '') : '',
        schedule_timezone: s.timezone || 'UTC',
        execute_now: false,
      });
      this.intervalValid = this.formData.schedule_interval
        ? this.validateInterval(this.formData.schedule_interval) : null;
      this.cronValid = this.formData.schedule_cron
        ? this.validateCron(this.formData.schedule_cron) : null;
      await this.loadSessionsForPicker();
      // Await model picker so a saved model name is shown once the dropdown fills in.
      await this.loadModelsForPicker();
      this.showModal = true;
    },

    closeModal() {
      this.showModal = false;
      this.resetForm();
    },

    setModelMode(mode) {
      this.formData.model_mode = mode;
      if (mode === 'default') this.formData.model = '';
    },

    setSessionMode(mode) {
      this.formData.session_mode = mode;
      if (mode === 'manual') this.loadSessionsForPicker();
    },

    async loadTasks() {
      this.loading = true;
      try {
        this.tasks = await BoarAiStore.api.getTasks();
      } catch (e) {
        BoarAiStore.actions.error(window.i18n?.t('notifications.loadTasksFailed') || 'Failed to load tasks');
      } finally {
        this.loading = false;
      }
    },

    async saveTask() {
      if (!this.canSubmit) {
        if (this.formData.kind === 'ai' && !this.formData.prompt?.trim()) {
          BoarAiStore.actions.error(this.t('tasks.aiTaskPromptRequired'));
        } else if (this.formData.kind === 'script' && !this.formData.script_path?.trim()) {
          BoarAiStore.actions.error(this.t('tasks.scriptTaskPathRequired'));
        } else if (this.formData.schedule_type === 'once' && !this.formData.schedule_run_at) {
          BoarAiStore.actions.error(this.t('tasks.selectRunTime'));
        } else if (this.formData.schedule_type === 'interval' && !this.validateInterval(this.formData.schedule_interval)) {
          BoarAiStore.actions.error(this.t('tasks.intervalFormatError'));
        } else if (this.formData.schedule_type === 'cron' && !this.validateCron(this.formData.schedule_cron)) {
          BoarAiStore.actions.error(this.t('tasks.cronFormatError'));
        }
        return;
      }

      this.formData.saving = true;
      try {
        const payload = {
          title: this.formData.title.trim(),
          description: this.formData.description || '',
          kind: this.formData.kind,
          max_retries: parseInt(this.formData.max_retries) || 0,
          timeout_seconds: parseInt(this.formData.timeout_seconds) || 0,
        };

        if (this.formData.kind === 'ai') {
          payload.prompt = this.formData.prompt.trim();
          if (this.formData.model_mode === 'specify' && this.formData.model?.trim()) {
            payload.model = this.formData.model.trim();
          }
        }
        if (this.formData.kind === 'script') {
          payload.script_path = this.formData.script_path.trim();
          if (this.formData.script_args?.trim())
            payload.script_args = this.formData.script_args.trim().split(/\s+/).filter(Boolean);
        }
        if (this.formData.session_mode) {
          payload.session_mode = this.formData.session_mode;
        }
        if (this.formData.session_id?.trim()) {
          payload.session_id = this.formData.session_id.trim();
        }

        const schedule = this.buildSchedulePayload();
        if (schedule) {
          payload.schedule = schedule;
        } else if (this.formData.isEdit && !this.formData.schedule_type) {
          payload.schedule = null;
        }

        if (this.formData.isEdit) {
          const updated = await BoarAiStore.api.updateTask(this.formData.editTaskId, payload);
          const idx = this.tasks.findIndex(t => t.task_id === this.formData.editTaskId);
          if (idx >= 0) this.tasks[idx] = { ...this.tasks[idx], ...updated };
        } else {
          const task = await BoarAiStore.api.createTask(payload);
          this.tasks.unshift(task);
        }
        this.closeModal();
      } catch (e) {
        BoarAiStore.actions.error(e.message || window.i18n?.t('notifications.saveTaskFailed') || 'Failed to save task');
      } finally {
        this.formData.saving = false;
      }
    },

    toggleDetail(task) {
      this.detailTaskId = this.detailTaskId===task.task_id?null:task.task_id;
      if (this.detailTaskId) BoarAiStore.api.getTaskTree(task.task_id).then(tree=>{if(tree)Object.assign(task,tree);}).catch(()=>{});
    },

    isTaskBusy(t) { return !!(this.busyTaskIds && this.busyTaskIds[t.task_id]); },
    async withTaskBusy(t, fn) {
      this.busyTaskIds = { ...(this.busyTaskIds||{}), [t.task_id]: true };
      try { await fn(); } finally { this.busyTaskIds = { ...(this.busyTaskIds||{}), [t.task_id]: false }; }
    },
    async executeTask(t) { await this.withTaskBusy(t, async () => { try{await BoarAiStore.api.executeTask(t.task_id);await this.loadTasks();}catch(e){BoarAiStore.actions.error(e.message);} }); },
    async cancelTask(t) { await this.withTaskBusy(t, async () => { try{await BoarAiStore.api.cancelTask(t.task_id);t.status='cancelled';}catch(e){BoarAiStore.actions.error(e.message);} }); },
    async pauseTask(t) { await this.withTaskBusy(t, async () => { try{await BoarAiStore.api.pauseTask(t.task_id);t.status='paused';}catch(e){BoarAiStore.actions.error(e.message);} }); },
    async resumeTask(t) { await this.withTaskBusy(t, async () => { try{await BoarAiStore.api.resumeTask(t.task_id);t.status='running';}catch(e){BoarAiStore.actions.error(e.message);} }); },
    async confirmDelete(t) {
      const confirmed = await BoarAiStore.actions.confirm({
        title: window.i18n?.t('notifications.deleteConfirmTitle') || 'Delete?',
        message: window.i18n?.t('notifications.deleteConfirmMessage') || 'Are you sure you want to delete this task?',
        confirmText: window.i18n?.t('common.delete') || 'Delete',
        type: 'danger',
      });
      if (!confirmed) return;
      await this.withTaskBusy(t, async () => {
        try { await BoarAiStore.api.deleteTask(t.task_id); this.tasks=this.tasks.filter(x=>x.task_id!==t.task_id); } catch(e) { BoarAiStore.actions.error(e.message); }
      });
    },

    async loadSessionsForPicker() {
      // Always reload so the picker sees newly-created sessions
      this.loadingSessions = true;
      try {
        const list = await BoarAiStore.api.getSessions({ limit: 100, sort: 'updated_at', order: 'desc' });
        // getSessions maps to {id,title,time,...} but the template expects session_id & updated_at
        this.sessions = (list || []).map(s => ({ ...s, session_id: s.id, updated_at: s.updated_at || s.time }));
      } catch (e) {
        console.error('Failed to load sessions:', e);
      } finally {
        this.loadingSessions = false;
      }
    },

    formatSessionTime(iso) {
      return window.UiKit ? window.UiKit.formatTime(iso, window.i18n) : '';
    },

    isActive(s){return['running','queued','paused'].includes(s);},
    canExecute(t){return!['running','queued'].includes(t.status);},
    canCancel(t){return['running','queued'].includes(t.status);},
    canPause(t){return t.status==='running';},
    canResume(t){return t.status==='paused';},
    formatTime(i){if(!i)return'-';try{return new Date(i).toLocaleString();}catch(e){return i;}},

    // Unified status meta: one map instead of 5 parallel getStatus* methods.
    getStatusMeta(s) {
      const meta = {
        pending:    { icon: 'clock',        color: 'text-amber-500', bg: 'bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-300', label: this.t('tasks.pending') },
        queued:     { icon: 'hourglass',    color: 'text-gray-500',  bg: 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-300', label: this.t('tasks.queued') },
        running:    { icon: 'loader-2',     color: 'text-blue-500 animate-spin', bg: 'bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300', label: this.t('tasks.executing') },
        paused:     { icon: 'pause-circle', color: 'text-amber-500', bg: 'bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-300', label: this.t('tasks.paused') },
        completed:  { icon: 'check-circle', color: 'text-green-500', bg: 'bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300', label: this.t('tasks.executionSuccess') },
        failed:     { icon: 'alert-circle', color: 'text-red-500',   bg: 'bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300', label: this.t('tasks.error') },
        cancelled:  { icon: 'x-circle',     color: 'text-gray-500',  bg: 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-300', label: this.t('tasks.cancelled') },
        skipped:    { icon: 'skip-forward', color: 'text-gray-500',  bg: 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-300', label: this.t('tasks.skipped') },
      };
      return meta[s] || { icon: 'circle', color: 'text-gray-500', bg: 'bg-gray-100 dark:bg-gray-700', label: s };
    },
    getStatusIcon(s) { return this.getStatusMeta(s).icon; },
    getStatusIconBg(s) { return this.getStatusMeta(s).bg; },
    getStatusIconColor(s) { return this.getStatusMeta(s).color; },
    getStatusBadge(s) { return this.getStatusMeta(s).bg; },
    getStatusLabel(s) { return this.getStatusMeta(s).label; },


  }
};
