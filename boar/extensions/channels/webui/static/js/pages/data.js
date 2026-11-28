const AGENT_ICON_OPTIONS = [
  'bot', 'code', 'pen-tool', 'clipboard-list', 'globe', 'brain',
  'sparkles', 'zap', 'star', 'heart', 'shield', 'crown',
  'users', 'user-cog', 'wand-2', 'magic', 'palette', 'music',
  'book-open', 'graduation-cap', 'microscope', 'atom', 'test-tube',
  'settings-2', 'sliders', 'filter', 'search', 'eye', 'bell'
];

// An agent name becomes a folder name on disk (.boarai/agents/<name>/), so the
// accepted set stays conservative: '.' / '/' / '\' would escape the directory.
const AGENT_NAME_RE = /^[A-Za-z0-9_\-\u4e00-\u9fa5]{1,48}$/;

/**
 * Data Page Component
 * 
 * Data management view with secondary navigation.
 * Uses TailwindCSS for styling.
 * Supports internationalization (i18n).
 * Uses Lucide Icons for all icons.
 */

const DataPage = {
  name: 'DataPage',
  
  template: `
    <div class="max-w-7xl mx-auto px-4 py-6 animate-fade-in flex flex-col flex-1 min-h-0">
      <!-- Secondary Navigation -->
      <div class="mb-6">
        <tab-bar :model-value="activeTab" :tabs="tabs"
                 @update:model-value="switchDataTab"></tab-bar>
      </div>

      <!-- Tab Content -->
      <div class="space-y-4">
        
        <!-- Owner Settings Tab -->
        <div v-if="activeTab === 'ownerSettings'" class="space-y-4">
          <card-section icon="user-cog" :title="t('data.ownerSettings')" body-class="p-4">
            <template #header-actions>
              <span v-if="ownerSaved" class="text-xs text-mint-500 flex items-center gap-1">
                <i data-lucide="check" class="w-3 h-3"></i>
                {{ t('common.saved') || 'Saved' }}
              </span>
            </template>
            <textarea v-model="userContent" rows="12"
                        class="w-full px-3 py-2 text-sm font-mono bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-all resize-y"
                        :placeholder="t('data.userContentHint') || 'Your profile content (saved to .boarai/user/user.md)'"></textarea>
              <p class="text-xs text-gray-500 mt-2">{{ t('data.userContentDesc') || 'This content is saved to .boarai/user/user.md and can be read by AI agents.' }}</p>
              <div class="flex items-center gap-3 pt-3">
                <button @click="saveOwnerSettings"
                        class="px-4 py-2 text-sm font-medium bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors flex items-center gap-2"
                        :disabled="ownerSaving">
                  <i v-if="ownerSaving" data-lucide="loader" class="w-4 h-4 animate-spin"></i>
                  <i v-else data-lucide="save" class="w-4 h-4"></i>
                  {{ t('common.save') || 'Save' }}
                </button>
                <button @click="loadOwnerSettings"
                        class="px-4 py-2 text-sm font-medium bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors flex items-center gap-2">
                  <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
                  {{ t('common.refresh') || 'Refresh' }}
                </button>
              </div>
          </card-section>
        </div>

        <!-- Role Management Tab (Agent Management) -->
        <div v-if="activeTab === 'roleManagement'" class="flex flex-col lg:flex-row flex-1 min-h-0 ${UiClasses.card} overflow-hidden" :style="{ minHeight: 'calc(100vh - 200px)' }">
          <!-- Left: Agent List -->
          <div class="lg:w-72 shrink-0">
            <div class="flex flex-col h-full border-r border-gray-200 dark:border-gray-700">
              <div class="px-3 py-2.5 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0">
                <h3 class="text-sm font-semibold text-gray-900 dark:text-white">{{ t('agents.title') }}</h3>
                <button @click="createNewAgent"
                        class="flex items-center gap-1 px-2 py-1 text-xs font-medium bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors">
                  <i data-lucide="plus" class="w-3 h-3"></i>
                  {{ t('agents.createAgent') }}
                </button>
              </div>
              <div class="p-2 flex-1 min-h-0 flex flex-col">
                <div class="relative mb-2 shrink-0">
                  <i data-lucide="search" class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-500"></i>
                  <input v-model="agentSearchQuery" type="text" :placeholder="t('agents.searchPlaceholder')"
                         class="w-full pl-8 pr-3 py-1.5 text-xs bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-600 rounded-lg focus:ring-1 focus:ring-primary/50 focus:border-primary">
                </div>
                <div v-if="agentLoading" class="text-center py-4 text-xs text-gray-500 shrink-0">{{ t('common.loading') }}</div>
                <div v-else class="space-y-0.5 flex-1 overflow-y-auto min-h-0">
                  <div v-for="agent in filteredAgents" :key="agent.name"
                       @click="onSelectAgent(agent)"
                       class="flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer transition-all text-xs"
                       :class="selectedAgent?.name === agent.name
                         ? 'bg-primary/10 text-primary ring-1 ring-primary/20'
                         : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50'">
                    <i :data-lucide="agent.avatar || 'bot'" class="w-4 h-4 shrink-0"></i>
                    <span class="truncate font-medium">{{ agent.name }}</span>
                    <span v-if="agent.is_default" class="ml-auto text-[11px] text-primary">⭐</span>
                  </div>
                  <div v-if="filteredAgents.length === 0" class="text-center py-4 text-xs text-gray-500">
                    {{ agentSearchQuery.trim() ? (t('agents.noMatch') || 'No matching agents') : t('agents.noAgents') }}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Right: Agent Editor -->
          <div class="flex-1 min-w-0 flex flex-col">
            <div v-if="!selectedAgent" class="flex-1 flex items-center justify-center p-8 text-center">
              <div>
                <i data-lucide="bot" class="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-3"></i>
                <p class="text-sm text-gray-500 dark:text-gray-400">{{ t('agents.selectHint') || 'Select an agent on the left to start editing' }}</p>
              </div>
            </div>
            <div v-else class="flex flex-col h-full overflow-hidden">
              <!-- Editor Header -->
              <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0">
                <h3 class="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                  <i :data-lucide="agentForm.avatar || 'bot'" class="w-4 h-4 text-primary"></i>
                  {{ isNewAgent ? (t('agents.createNewAgent') || 'New Agent') : (t('agents.editAgent') || 'Edit') + ': ' + selectedAgent.name }}
                  <span v-if="selectedAgent?.is_default" class="text-[11px] px-1.5 py-0.5 bg-primary/10 text-primary rounded-full">{{ t('common.default') || 'Default' }}</span>
                  <span v-if="agentDirty" class="text-[11px] font-normal text-sun-500">{{ t('common.unsaved') || 'Unsaved' }}</span>
                </h3>
                <div class="flex items-center gap-2">
                  <!-- Any agent may be deleted (the backend reassigns the
                       default flag); only the last remaining one is protected. -->
                  <button v-if="!isNewAgent" @click="deleteAgent(selectedAgent)"
                          :disabled="agentSaving || agentList.length <= 1"
                          :title="deleteAgentTooltip"
                          :aria-label="deleteAgentTooltip"
                          class="px-2.5 py-1.5 text-xs font-medium bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                    <i data-lucide="trash-2" class="w-3 h-3 inline"></i>
                  </button>
                  <button v-if="selectedAgent && !selectedAgent.is_default" @click="setAsDefault(selectedAgent)"
                          :disabled="agentSaving"
                          :title="t('agents.setDefault')"
                          :aria-label="t('agents.setDefault')"
                          class="px-2.5 py-1.5 text-xs font-medium bg-sun-50 dark:bg-sun-900/20 text-sun-700 dark:text-sun-300 rounded-lg hover:bg-sun-100 dark:hover:bg-sun-900/40 transition-colors flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed">
                    <i data-lucide="star" class="w-3 h-3"></i>
                  </button>
                  <button @click="saveAgent"
                          class="px-3 py-1.5 text-xs font-medium bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors flex items-center gap-1"
                          :disabled="agentSaving">
                    <i v-if="agentSaving" data-lucide="loader" class="w-3 h-3 animate-spin"></i>
                    <i v-else data-lucide="save" class="w-3 h-3"></i>
                    {{ t('common.save') || 'Save' }}
                  </button>
                </div>
              </div>
              <!-- Editor Body -->
              <div class="p-4 space-y-4 flex-1 overflow-y-auto min-h-0">
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('agents.name') }} <span class="text-error">*</span></label>
                    <input v-model="agentForm.name" :disabled="!isNewAgent" type="text" maxlength="48"
                           :placeholder="t('agents.namePlaceholder')" :aria-invalid="!!agentNameError"
                           class="w-full ${UiClasses.inputSm} disabled:opacity-50"
                           :class="agentNameError ? 'border-red-400 focus:border-red-400 focus:ring-red-400/40' : ''">
                    <p v-if="agentNameError" class="mt-1 text-[11px] text-red-500">{{ agentNameError }}</p>
                  </div>
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('agents.avatar') }}</label>
                    <select v-model="agentForm.avatar"
                            class="w-full ${UiClasses.inputSm}">
                      <option v-for="icon in agentIconOptions" :key="icon" :value="icon">{{ icon }}</option>
                    </select>
                  </div>
                </div>
                <!-- Soul (Personality) -->
                <div>
                  <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('agents.soul') || 'Personality (soul.md)' }}</label>
                  <textarea v-model="agentForm.zone1_identity" rows="5" :placeholder="t('agents.identityPlaceholder')"
                            class="w-full px-2.5 py-1.5 text-xs font-mono bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-600 rounded-lg focus:ring-1 focus:ring-primary/50 focus:border-primary resize-y"></textarea>
                </div>
                <!-- Tool & path bounds. Approval prompting is a session
                     concern (session.security_overrides), so an agent only
                     narrows *which* tools it may use. -->
                <div class="space-y-3 border-t border-gray-100 dark:border-gray-700 pt-3">
                  <div>
                    <div class="flex items-center justify-between mb-1">
                      <label class="block text-xs font-medium text-gray-700 dark:text-gray-300">{{ t('agents.allowedTools') || 'Allowed Tools' }}</label>
                      <span class="text-[11px]"
                            :class="!isAllTools && selectedToolCount === 0 ? 'text-sun-500' : 'text-gray-500 dark:text-gray-400'">
                        {{ isAllTools
                          ? (t('agents.allTools') || 'All tools')
                          : (selectedToolCount === 0
                              ? (t('agents.noToolSelected') || 'No tool selected — this agent cannot call any tool.')
                              : t('agents.selectedCount', { count: selectedToolCount })) }}
                      </span>
                    </div>
                    <!-- Pick from the live tool catalogue instead of typing
                         names: a typo used to remove a tool silently. Compact
                         chips keep 35 tools scannable without a tall list. -->
                    <div class="flex items-center gap-2 mb-2">
                      <label class="flex items-center gap-1.5 px-2 py-1 text-xs rounded-lg border cursor-pointer select-none shrink-0"
                             :title="t('agents.allToolsHint') || 'Allow every tool, including ones added later'"
                             :class="isAllTools
                               ? 'bg-primary/5 border-primary/30 text-primary'
                               : 'bg-gray-50 dark:bg-gray-700/50 border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400'">
                        <input type="checkbox" :checked="isAllTools" @change="toggleAllTools($event.target.checked)" class="accent-primary">
                        {{ t('agents.allTools') || 'All tools' }}
                      </label>
                      <input v-if="!isAllTools" v-model="toolSearch" type="text"
                             :placeholder="t('agents.searchTools') || 'Search tools...'"
                             class="flex-1 min-w-0 px-2 py-1 text-xs bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-600 rounded-lg focus:ring-1 focus:ring-primary/50 focus:border-primary">
                    </div>
                    <div v-if="!isAllTools" class="rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 p-2 max-h-48 overflow-y-auto space-y-2">
                      <div v-if="toolLoading" class="px-1 py-2 text-xs text-gray-500">{{ t('common.loading') }}</div>
                      <template v-else>
                        <div v-for="group in groupedTools" :key="group.key">
                          <div class="mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-400">{{ group.label }}</div>
                          <div class="flex flex-wrap gap-1">
                            <button v-for="tool in group.tools" :key="tool.name" type="button"
                                    :title="tool.description"
                                    :aria-pressed="isToolSelected(tool.name)"
                                    @click="toggleTool(tool.name, !isToolSelected(tool.name))"
                                    class="px-2 py-0.5 text-[11px] font-mono rounded-md border transition-colors"
                                    :class="isToolSelected(tool.name)
                                      ? 'bg-primary/10 text-primary border-primary/40'
                                      : 'bg-gray-50 dark:bg-gray-700/40 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-600 hover:border-primary/40 hover:text-primary'">
                              {{ tool.name }}
                            </button>
                          </div>
                        </div>
                        <div v-if="filteredTools.length === 0" class="px-1 py-3 text-center text-xs text-gray-500">
                          {{ t('agents.noToolMatch') || 'No matching tools' }}
                        </div>
                      </template>
                    </div>
                  </div>
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('agents.allowedPaths') || 'Allowed File Paths' }}</label>
                    <textarea v-model="agentForm.allowed_paths_text" rows="1"
                              :placeholder="t('agents.allowedPathsHint')"
                              class="w-full px-2.5 py-1.5 text-xs font-mono bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-600 rounded-lg focus:ring-1 focus:ring-primary/50 focus:border-primary resize-none"></textarea>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Memory Management Tab -->
        <div v-if="activeTab === 'memoryManagement'" class="space-y-4">
          <!-- Stats Cards (by memory type). Two columns under 420px squeeze
               the labels into 3-4 lines, so polish.css drops .data-stat-grid
               to one column there. -->
          <div class="data-stat-grid grid grid-cols-2 lg:grid-cols-4 gap-4">
            <div v-for="tc in memTypeCards" :key="tc.key" class="${UiClasses.card} p-4">
              <div class="flex items-center justify-between mb-2">
                <span class="text-sm text-gray-500 dark:text-gray-400">{{ memTypeLabel(tc.key) }}</span>
                <span class="w-3 h-3 rounded-full" :class="tc.dot"></span>
              </div>
              <div class="text-2xl font-bold text-gray-900 dark:text-white">{{ memStats.by_type?.[tc.key] || 0 }}</div>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex items-center gap-2">
            <button @click="loadMemoryData" :disabled="memLoading"
                    class="px-4 py-2 text-sm font-medium bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors">
              {{ t('common.refresh') || 'Refresh' }}
            </button>
            <button @click="doConsolidate" :disabled="memLoading"
                    class="px-4 py-2 text-sm font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors">
              {{ t('memory.consolidate') || 'Consolidate' }}
            </button>
          </div>

          <!-- Staging List -->
          <card-section v-if="stagingList.length > 0" :title="t('memory.staging')" body-class="p-0">
            <div class="divide-y divide-gray-100 dark:divide-gray-700">
              <div v-for="item in stagingList" :key="item.id" class="px-4 py-3 flex items-center justify-between">
                <div class="flex-1 min-w-0 mr-4">
                  <p class="text-sm text-gray-900 dark:text-white truncate">{{ item.epitaph ? item.epitaph.substring(0, 120) : item.id }}</p>
                  <p class="text-xs text-gray-500 mt-1">
                    {{ item.source_pod || '' }} · {{ item.status || 'pending' }}
                    <span v-if="item.confidence" class="ml-2">置信度: {{ (item.confidence * 100).toFixed(0) }}%</span>
                  </p>
                </div>
                <div class="flex items-center gap-2">
                  <button @click="confirmStagingItem(item)" class="px-3 py-1.5 text-xs font-medium bg-mint-700 text-white rounded hover:bg-mint-800">{{ t('memory.stagingConfirm') }}</button>
                  <button @click="rejectStagingItem(item)" class="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded hover:bg-red-600">{{ t('memory.stagingReject') }}</button>
                </div>
              </div>
            </div>
          </card-section>

          <!-- Memory Entries Search -->
          <card-section icon="brain" :title="t('memory.entries')" body-class="p-3">
            <div class="flex items-center gap-2">
              <input v-model="memSearch" @keyup.enter="searchMemory" type="text"
                     :placeholder="t('memory.search') || 'Search...'"
                     class="flex-1 px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-primary/50">
              <select v-model="memTypeFilter" @change="searchMemory"
                      class="px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-900 dark:text-white">
                <option value="">{{ t('common.all') || 'All' }}</option>
                <option value="fact">{{ memTypeLabel('fact') }}</option>
                <option value="decision">{{ memTypeLabel('decision') }}</option>
                <option value="preference">{{ memTypeLabel('preference') }}</option>
                <option value="insight">{{ memTypeLabel('insight') }}</option>
                <option value="lesson">{{ memTypeLabel('lesson') }}</option>
              </select>
            </div>
            <div class="divide-y divide-gray-100 dark:divide-gray-700 max-h-[calc(100vh-240px)] overflow-y-auto">
              <div v-if="memEntries.length === 0 && !memLoading" class="px-4 py-8 text-center text-gray-500 text-sm">
                {{ t('memory.noEntries') || 'No memory entries' }}
              </div>
              <div v-for="entry in memEntries" :key="entry.entry_id" class="group px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-750">
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="px-2 py-0.5 text-xs rounded-full font-medium" :class="memTypeClass(entry.entry_type)">
                      {{ memTypeLabel(entry.entry_type) }}
                    </span>
                    <span v-for="tag in memVisibleTags(entry)" :key="tag"
                          class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                      {{ memTagLabel(tag) }}
                    </span>
                    <span v-if="entry.metadata?.channel" class="text-[11px] text-gray-400 dark:text-gray-400">
                      {{ memChannelLabel(entry.metadata.channel) }}
                    </span>
                    <span v-if="entry.importance" class="text-[11px] text-sun-500">★ {{ entry.importance.toFixed(1) }}</span>
                  </div>
                  <p class="text-sm text-gray-800 dark:text-gray-200 mt-1.5 leading-relaxed break-words">{{ entry.content ? entry.content.substring(0, 200) : '' }}</p>
                  <div class="flex items-center justify-between gap-2 mt-1">
                    <div class="flex items-center gap-2 min-w-0 text-[11px] text-gray-400 dark:text-gray-400">
                      <span class="truncate">{{ memPodPath(entry.pod_uri) }}</span>
                      <a href="#" @click.prevent="copyPodUri(entry)"
                         class="shrink-0 text-primary hover:underline font-mono truncate max-w-[130px]"
                         :title="entry.pod_uri">{{ entry.pod_uri }}</a>
                    </div>
                    <span class="shrink-0 text-[11px] text-gray-400 dark:text-gray-400">{{ formatDateTime(entry.created_at) }}</span>
                  </div>
                  <div class="flex items-center justify-end gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button @click="editMemoryEntry(entry)" class="px-2 py-0.5 text-[11px] rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300">
                      {{ t('memory.edit') || 'Edit' }}
                    </button>
                    <button @click="deleteMemoryEntry(entry)" class="px-2 py-0.5 text-[11px] rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-red-500">
                      {{ t('memory.delete') || 'Delete' }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </card-section>
        </div>

        <!-- Recycle Bin Tab -->
        <div v-if="activeTab === 'recycleBin'" class="space-y-4">
          <!-- Stats -->
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div class="${UiClasses.card} p-4 text-center">
              <div class="text-2xl font-bold text-primary-500">{{ trashStats?.by_type?.session || 0 }}</div>
              <div class="text-xs text-gray-500 mt-1">{{ t('recycleBin.typeSession') }}</div>
            </div>
            <div class="${UiClasses.card} p-4 text-center">
              <div class="text-2xl font-bold text-mint-700 dark:text-mint-300">{{ trashStats?.by_type?.agent || 0 }}</div>
              <div class="text-xs text-gray-500 mt-1">{{ t('recycleBin.typeAgent') }}</div>
            </div>
            <div class="${UiClasses.card} p-4 text-center">
              <div class="text-2xl font-bold text-sun-700 dark:text-sun-300">{{ trashStats?.by_type?.file || 0 }}</div>
              <div class="text-xs text-gray-500 mt-1">{{ t('recycleBin.typeFile') }}</div>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex items-center gap-2">
            <select v-model="trashFilter" @change="loadTrash"
                    class="px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-900 dark:text-white">
              <option value="">{{ t('common.all') || 'All' }}</option>
              <option value="session">{{ t('recycleBin.typeSession') }}</option>
              <option value="agent">{{ t('recycleBin.typeAgent') }}</option>
              <option value="file">{{ t('recycleBin.typeFile') }}</option>
            </select>
            <button @click="loadTrash" class="px-4 py-2 text-sm font-medium bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors">
              {{ t('common.refresh') || 'Refresh' }}
            </button>
            <button v-if="trashList.length > 0" @click="emptyAllTrash"
                    class="px-4 py-2 text-sm font-medium bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors">
              {{ t('recycleBin.purgeAll') }}
            </button>
          </div>

          <!-- Trash List -->
          <div class="${UiClasses.card} overflow-hidden">
            <div v-if="trashList.length === 0 && !trashLoading" class="px-4 py-8 text-center text-gray-500 text-sm">
              <i data-lucide="inbox" class="w-12 h-12 mx-auto mb-3 opacity-30"></i>
              <p>{{ t('recycleBin.empty') || 'Recycle Bin is empty' }}</p>
            </div>
            <div class="divide-y divide-gray-100 dark:divide-gray-700 max-h-[500px] overflow-y-auto">
              <div v-for="item in trashList" :key="item.trash_id" class="px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-750">
                <div class="flex items-center justify-between">
                  <div class="flex-1 min-w-0 mr-3">
                    <div class="flex items-center gap-2">
                      <span class="text-xs px-2 py-0.5 rounded-full font-medium"
                            :class="typeBadgeClass(item.type)">
                        {{ typeLabel(item.type) }}
                      </span>
                      <span v-if="item.distill_mode === 'pending'" class="text-xs px-2 py-0.5 rounded-full bg-sun-100 text-sun-700 dark:bg-sun-900/30 dark:text-sun-300">
                        {{ t('recycleBin.distillPending') }}
                      </span>
                      <span v-if="item.distill_mode === 'distilled'" class="text-xs px-2 py-0.5 rounded-full bg-mint-100 text-mint-700 dark:bg-mint-900/30 dark:text-mint-300">
                        {{ t('recycleBin.distilled') }}
                      </span>
                    </div>
                    <p class="text-sm font-medium text-gray-900 dark:text-white mt-1 truncate">{{ trashItemTitle(item) }}</p>
                    <p class="mt-0.5 flex items-center gap-1.5 text-[11px] leading-4 text-gray-400 dark:text-gray-400 min-w-0">
                      <span v-if="item.type === 'session' && trashItemId(item)" class="font-mono truncate">{{ trashItemId(item) }}</span>
                      <span v-if="item.deleted_at" class="shrink-0">{{ formatTrashTime(item.deleted_at) }}</span>
                    </p>
                  </div>
                  <div class="flex items-center gap-1.5">
                    <button @click="restoreTrashItem(item)" class="px-2.5 py-1.5 text-xs font-medium bg-mint-700 text-white rounded hover:bg-mint-800">
                      {{ t('recycleBin.restore') }}
                    </button>
                    <button v-if="item.distill_mode !== 'distilled' && (item.type === 'session' || item.type === 'agent')"
                            @click="distillTrashItem(item)" class="px-2.5 py-1.5 text-xs font-medium bg-primary text-white rounded hover:bg-primary-dark">
                      {{ t('recycleBin.distill') }}
                    </button>
                    <button @click="purgeTrashItem(item)" class="px-2.5 py-1.5 text-xs font-medium bg-red-500 text-white rounded hover:bg-red-600">
                      {{ t('recycleBin.purge') }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Usage Stats Tab (Real API Data) -->
        <div v-if="activeTab === 'usageStats'" class="space-y-4">
          <!-- Actions row: unified range selector (left) + clear (right) -->
          <div class="flex items-center justify-between gap-2">
            <div class="flex gap-px bg-gray-100 dark:bg-gray-700/50 rounded-md p-px shrink-0">
              <button v-for="r in statRanges" :key="r.id"
                      @click="statRange = r.id"
                      class="px-2.5 py-1 rounded text-xs font-medium leading-tight transition-all whitespace-nowrap"
                      :class="statRange === r.id
                        ? 'bg-white dark:bg-gray-600 shadow-sm text-gray-900 dark:text-white'
                        : 'text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'">
                {{ r.label }}
              </button>
            </div>
            <button @click="clearUsageStats"
                    class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/10 hover:bg-red-100 dark:hover:bg-red-500/20 transition-colors">
              <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
              {{ t('data.clearStats') }}
            </button>
          </div>

          <!-- KPI cards: Today / Input / Output / Cache hit rate -->
          <div class="data-stat-grid grid grid-cols-2 lg:grid-cols-4 gap-4">
            <!-- Total consumption (follows selected range) -->
            <div class="${UiClasses.card} p-4 relative overflow-hidden">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <div class="text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('data.totalTokenUsage') }}</div>
                  <div class="mt-1.5 text-2xl font-bold text-gray-900 dark:text-white leading-none">{{ formatNumber((stats.range?.input || 0) + (stats.range?.completion || 0)) }}</div>
                </div>
                <div class="w-9 h-9 rounded-lg bg-sun-500/10 flex items-center justify-center shrink-0">
                  <i data-lucide="coins" class="w-5 h-5 text-sun-500"></i>
                </div>
              </div>
              <div class="mt-3 flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
                <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700/60">{{ statRangeLabel }}</span>
                <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700/60">{{ stats.range?.requests || 0 }} {{ t('data.requestsUnit') }}</span>
              </div>
            </div>
            <!-- Input tokens (range) -->
            <div class="${UiClasses.card} p-4 relative overflow-hidden">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <div class="text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('data.inputTokens') }}</div>
                  <div class="mt-1.5 text-2xl font-bold text-gray-900 dark:text-white leading-none">{{ formatNumber(stats.range?.input ?? stats.tokens?.total ?? 0) }}</div>
                </div>
                <div class="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                  <i data-lucide="arrow-down-to-line" class="w-5 h-5 text-primary"></i>
                </div>
              </div>
              <div class="mt-3 flex items-center justify-between gap-2">
                <span class="text-[11px] text-gray-500 dark:text-gray-400 whitespace-nowrap">{{ t('data.readFromCache') }} {{ formatNumber(stats.range?.cache_read ?? stats.cache?.total_read ?? 0) }}</span>
                <sparkline :data="stats.tokens?.trend || [0,0,0,0,0,0,0]" :color="chartColor" class="w-20 shrink-0"></sparkline>
              </div>
            </div>
            <!-- Output tokens (range) -->
            <div class="${UiClasses.card} p-4 relative overflow-hidden">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <div class="text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('data.completionTokens') }}</div>
                  <div class="mt-1.5 text-2xl font-bold text-gray-900 dark:text-white leading-none">{{ formatNumber(stats.range?.completion ?? stats.tokens?.completion_total ?? 0) }}</div>
                </div>
                <div class="w-9 h-9 rounded-lg bg-primary-500/10 flex items-center justify-center shrink-0">
                  <i data-lucide="arrow-up-from-line" class="w-5 h-5 text-primary-500"></i>
                </div>
              </div>
              <div class="mt-3 flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
                <span>{{ statRangeLabel }} · {{ t('data.completionHint') }}</span>
              </div>
            </div>
            <!-- Cache hit rate (range) -->
            <div class="${UiClasses.card} p-4 relative overflow-hidden">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <div class="text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('data.cacheHitRate') }}</div>
                  <div class="mt-1.5 text-2xl font-bold text-gray-900 dark:text-white leading-none">{{ (stats.range?.hit_rate ?? stats.cache?.hit_rate ?? 0).toFixed(1) }}<span class="text-sm font-medium text-gray-400">%</span></div>
                </div>
                <div class="w-9 h-9 rounded-lg bg-primary-500/10 flex items-center justify-center shrink-0">
                  <i data-lucide="zap" class="w-5 h-5 text-primary-500"></i>
                </div>
              </div>
              <div class="mt-3 flex items-center justify-between gap-2">
                <span class="text-[11px] text-gray-500 dark:text-gray-400 whitespace-nowrap">{{ t('data.cacheWrite') }} {{ formatNumber(stats.range?.cache_created ?? stats.cache?.total_created ?? 0) }}</span>
                <sparkline :data="stats.cache?.trend || [0,0,0,0,0,0,0]" color="#2563eb" class="w-20 shrink-0"></sparkline>
              </div>
            </div>
          </div>
          
          <!-- Charts -->
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <!-- Token Usage Chart (range switchable: 7d / 30d / monthly) -->
            <div class="${UiClasses.card} p-4">
              <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-4">{{ t('data.tokenUsageTrend') }}</h3>
              <line-chart :data="tokenChartData" :height="200" :color="chartColor" :show-area="true"></line-chart>
            </div>
            
            <!-- Cache Hit Rate Over Time -->
            <div class="${UiClasses.card} p-4">
              <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-4">{{ t('data.cacheHitRateTrend') }}</h3>
              <line-chart :data="cacheRateChartData" :height="200" color="#2563eb" :show-area="true" unit="%"></line-chart>
            </div>
          </div>

          <!-- Breakdowns: per-model & per-provider -->
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div class="${UiClasses.card} p-4">
              <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-4">{{ t('data.byModel') }}</h3>
              <div v-if="(stats.per_model || []).length === 0"
                   class="text-xs text-gray-400 dark:text-gray-400 py-8 text-center">{{ t('data.noData') }}</div>
              <div v-else class="space-y-3">
                <div v-for="m in stats.per_model.slice(0, 6)" :key="m.model">
                  <div class="flex items-center justify-between gap-2 mb-1">
                    <span class="text-xs font-medium text-gray-700 dark:text-gray-300 truncate" :title="m.model">{{ m.model }}</span>
                    <span class="text-xs text-gray-500 shrink-0 whitespace-nowrap">{{ formatNumber(m.input) }} · {{ m.requests }} {{ t('data.requestsUnit') }} · {{ m.hit_rate }}%</span>
                  </div>
                  <div class="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                    <div class="h-full rounded-full transition-all duration-300"
                         :style="{ width: modelBarWidth(m) + '%', background: chartColor }"></div>
                  </div>
                  <div class="mt-0.5 text-[10px] text-gray-400 dark:text-gray-400 truncate">{{ m.provider }}</div>
                </div>
              </div>
            </div>
            <div class="${UiClasses.card} p-4">
              <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-4">{{ t('data.byProvider') }}</h3>
              <div v-if="providerDonutData.length === 0"
                   class="text-xs text-gray-400 dark:text-gray-400 py-8 text-center">{{ t('data.noData') }}</div>
              <donut-chart v-else :data="providerDonutData" :size="150" :thickness="24"></donut-chart>
            </div>

            <!-- Per-session usage breakdown -->
            <div class="${UiClasses.card} p-4">
              <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-4">{{ t('data.sessionUsage') }}</h3>
              <div v-if="perSessionSorted.length === 0"
                   class="text-xs text-gray-400 dark:text-gray-400 py-8 text-center">{{ t('data.noData') }}</div>
              <div v-else class="space-y-3">
                <div v-for="s in perSessionSorted" :key="s.session_id">
                  <div class="flex items-center justify-between gap-2 mb-1">
                    <span class="flex items-center gap-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 min-w-0">
                      <i data-lucide="message-square" class="w-3 h-3 shrink-0 text-primary"></i>
                      <span class="truncate" :title="s.title || s.session_id">{{ s.title || s.session_id }}</span>
                    </span>
                    <span class="text-xs text-gray-500 shrink-0 whitespace-nowrap">{{ formatNumber(s.input || 0) }} · {{ s.requests }} {{ t('data.requestsUnit') }} · {{ s.hit_rate || 0 }}%</span>
                  </div>
                  <div class="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                    <div class="h-full rounded-full transition-all duration-300"
                         :style="{ width: sessionBarWidth(s) + '%', background: chartColor }"></div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Daily activity heatmap (GitHub-style compact calendar) -->
            <div class="${UiClasses.card} p-4 relative" ref="activityCard">
              <div class="flex items-center justify-between gap-2 mb-3">
                <h3 class="text-sm font-semibold text-gray-900 dark:text-white">{{ t('data.dailyActivity') }}</h3>
                <div class="flex items-center gap-1 text-[10px] text-gray-400 dark:text-gray-400">
                  <span>{{ t('data.less') }}</span>
                  <span v-for="lv in [0,1,2,3,4]" :key="'lg-'+lv"
                        class="w-2.5 h-2.5 rounded-full"
                        :class="lv === 0 ? 'bg-gray-100 dark:bg-gray-700/60' : ''"
                        :style="lv > 0 ? { background: 'rgb(var(--primary-rgb) / ' + [0,0.25,0.45,0.7,1][lv] + ')' } : {}"></span>
                  <span>{{ t('data.more') }}</span>
                </div>
              </div>
              <div v-if="!activityHasData"
                   class="flex flex-col items-center justify-center py-10 text-center">
                <i data-lucide="calendar-x-2" class="w-8 h-8 text-gray-300 dark:text-gray-600 mb-3"></i>
                <span class="text-xs text-gray-400 dark:text-gray-400">{{ t('data.noData') }}</span>
              </div>
              <div v-else class="inline-block">
                <!-- Calendar grid: rows = weeks, columns = weekday (Sun..Sat) -->
                <div class="grid gap-1 mb-1.5 text-[10px] text-gray-400 dark:text-gray-400 text-center"
                     style="grid-template-columns: repeat(7, 1.75rem)">
                  <span v-for="(d, i) in weekdayLabels" :key="'wd-'+i">{{ d }}</span>
                </div>
                <div class="space-y-1">
                  <div v-for="(week, wi) in activityWeeks" :key="'wk-'+wi"
                       class="grid gap-1" style="grid-template-columns: repeat(7, 1.75rem)">
                    <div v-for="(cell, di) in week" :key="'wc-'+wi+'-'+di"
                         class="w-7 h-7 rounded-md cursor-default transition-colors"
                         :class="[cell === null ? 'opacity-0 pointer-events-none' : '', (cell && activityLevel(cell.input) === 0) ? 'bg-gray-100 dark:bg-gray-700/60' : '']"
                         :style="cell && activityLevel(cell.input) > 0 ? { background: 'rgb(var(--primary-rgb) / ' + [0,0.25,0.45,0.7,1][activityLevel(cell.input)] + ')' } : {}"
                         @mouseenter="onActivityHover($event, cell)"
                         @mouseleave="activityHover = null"></div>
                  </div>
                </div>
              </div>
              <!-- Heatmap tooltip -->
              <div v-if="activityHover"
                   class="absolute z-10 pointer-events-none px-3 py-2 rounded-lg bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 shadow-lg text-xs w-44"
                   :style="{ left: activityHover.x + 'px', top: activityHover.y + 'px' }">
                <div class="font-semibold text-gray-900 dark:text-white mb-1.5">{{ activityHover.date }}</div>
                <div class="flex items-center justify-between gap-4">
                  <span class="flex items-center gap-1.5 text-gray-500 dark:text-gray-400">
                    <span class="w-1.5 h-1.5 rounded-full" :style="{ background: chartColor }"></span>
                    {{ t('data.tokenTotal') }}
                  </span>
                  <span class="font-medium text-gray-900 dark:text-white">{{ formatNumber(activityHover.input) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Memory entry edit modal -->
        <div v-if="memEditVisible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="closeMemEdit">
          <div class="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-[540px] max-w-[92vw] max-h-[85vh] overflow-y-auto">
            <div class="flex items-center justify-between px-5 py-3 border-b border-gray-200 dark:border-gray-700">
              <h3 class="text-base font-semibold text-gray-900 dark:text-white">{{ t('memory.editTitle') || 'Edit Memory' }}</h3>
              <button @click="closeMemEdit" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none px-1">&times;</button>
            </div>
            <div class="p-5 space-y-4">
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('memory.entryType') || 'Type' }}</label>
                <select v-model="memEditForm.entry_type" class="w-full px-3 py-2 text-sm bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg text-gray-900 dark:text-white">
                  <option value="fact">{{ memTypeLabel('fact') }}</option>
                  <option value="decision">{{ memTypeLabel('decision') }}</option>
                  <option value="preference">{{ memTypeLabel('preference') }}</option>
                  <option value="pattern">{{ memTypeLabel('pattern') }}</option>
                  <option value="insight">{{ memTypeLabel('insight') }}</option>
                  <option value="procedure">{{ memTypeLabel('procedure') }}</option>
                  <option value="summary">{{ memTypeLabel('summary') }}</option>
                  <option value="lesson">{{ memTypeLabel('lesson') }}</option>
                </select>
              </div>
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('memory.topic') || 'Topic' }}</label>
                <input v-model="memEditForm.topic" type="text" class="w-full px-3 py-2 text-sm bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg text-gray-900 dark:text-white" />
              </div>
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('memory.content') || 'Content' }}</label>
                <textarea v-model="memEditForm.content" rows="4" class="w-full px-3 py-2 text-sm bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg text-gray-900 dark:text-white"></textarea>
              </div>
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('memory.tags') || 'Tags' }}</label>
                <input v-model="memEditForm.tags" type="text" class="w-full px-3 py-2 text-sm bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg text-gray-900 dark:text-white" />
                <p class="mt-1 text-[11px] text-gray-400">{{ t('memory.tagsHint') || 'Comma-separated tags' }}</p>
              </div>
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('memory.importance') || 'Importance' }}: {{ memEditForm.importance }}</label>
                <input v-model.number="memEditForm.importance" type="range" min="0" max="1" step="0.05" class="w-full" />
              </div>
            </div>
            <div class="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-200 dark:border-gray-700">
              <button @click="closeMemEdit" class="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">
                {{ t('common.cancel') || 'Cancel' }}
              </button>
              <button @click="saveMemoryEntry" :disabled="memEditSaving" class="px-4 py-2 text-sm font-medium bg-primary text-white rounded-lg hover:bg-primary-dark disabled:opacity-50">
                {{ memEditSaving ? (t('common.saving') || 'Saving...') : (t('memory.save') || 'Save') }}
              </button>
            </div>
          </div>
        </div>

      </div>
    </div>
  `,
  
  data() {
    return {
      activeTab: localStorage.getItem('boarai-tab-data') || 'usageStats',
      tabs: [],
      
      // Stats data (populated from real API)
      stats: {
        tokens: { total: 0, today: 0, hit_rate: 0, today_hit_rate: 0, trend: [0,0,0,0,0,0,0],
                  month: 0, month_hit_rate: 0, completion_total: 0,
                  today_completion: 0, today_requests: 0, requests: 0 },
        cache: { total_read: 0, total_created: 0, hit_rate: 0, trend: [0,0,0,0,0,0,0] },
        sessions: { total: 0, active: 0 },
        recent_requests: [],
        trend_30d: [],
        trend_today: [],
        trend_today_rate: [],
        cache_trend_30d: [],
        range: { input: 0, completion: 0, cache_read: 0, cache_created: 0,
                 requests: 0, hit_rate: 0, savings_usd: 0 },
        monthly: [],
        per_model: [],
        per_provider: [],
        per_session: [],
        daily_activity: [],
      },
      statRange: 'today',
      activityHover: null,
      
      // Agent management state
      agentSearchQuery: '',
      agentList: [],
      agentLoading: true,
      agentSaving: false,
      agentNameError: '',
      // Serialized form as last loaded/saved — drives the "unsaved" badge and
      // the discard guard when switching agents.
      agentSnapshot: '',
      // Tool picker for "allowed tools" (live catalogue from GET /api/tools)
      toolList: [],
      toolLoading: false,
      toolSearch: '',

      // Memory management state
      memStats: { total_tags: 0, total_entries_indexed: 0, by_type: {} },
      memTypeCards: [
        { key: 'fact', dot: 'bg-primary-500', labelKey: 'memory.typeFact' },
        { key: 'decision', dot: 'bg-sun-500', labelKey: 'memory.typeDecision' },
        { key: 'preference', dot: 'bg-mint-500', labelKey: 'memory.typePreference' },
        { key: 'insight', dot: 'bg-primary-500', labelKey: 'memory.typeInsight' },
        { key: 'lesson', dot: 'bg-red-500', labelKey: 'memory.typeLesson' },
      ],
      memEntries: [],
      memSearch: '',
      memTypeFilter: '',
      memLoading: false,
      memEditVisible: false,
      memEditSaving: false,
      memEditForm: { entry_id: '', pod_uri: '', topic: '', content: '', entry_type: 'fact', tags: '', importance: 0.5 },
      stagingList: [],
      stagingCount: 0,
      autoConfirmedCount: 0,

      // Recycle bin state
      trashList: [],
      trashStats: { total: 0, by_type: {} },
      trashFilter: '',
      trashLoading: false,
      isNewAgent: false,
      selectedAgent: null,
      agentIconOptions: AGENT_ICON_OPTIONS,
      agentForm: this.getEmptyForm(),
      // User profile
      userContent: '',
      ownerSaving: false,
      ownerSaved: false,
      ownerLoadError: false,
    };
  },
  
  async mounted() {
    this.updateTabLabels();
    await this.fetchStats();
    await this.loadAgents();
    await this.loadTools();
    await this.loadOwnerSettings();
    // Load data for the tab restored from localStorage so a page refresh
    // shows content immediately (previously only tab switching triggered load).
    if (this.activeTab === 'memoryManagement') this.loadMemoryData();
    if (this.activeTab === 'recycleBin') this.loadTrash();
  },

  // Icons hydrate automatically via app.js MutationObserver (childList + attr).

  watch: {
    // Unified range selector refetches range-filtered breakdowns
    statRange() {
      this.fetchStats();
    },
    // Clear the inline name error as soon as the user fixes the input —
    // otherwise the red hint stays until the next save attempt.
    'agentForm.name'() {
      this.agentNameError = '';
    },
  },

  computed: {
    chartColor() {
      return window.UiKit ? window.UiKit.primaryHex() : '#2563eb';
    },
    statRanges() {
      return [
        { id: 'today', label: this.t('data.rangeToday') || 'Today' },
        { id: '7d', label: this.t('data.range7d') || '7D' },
        { id: '30d', label: this.t('data.range30d') || '30D' },
      ];
    },
    tokenChartData() {
      if (this.statRange === 'today') {
        const hourly = this.stats.trend_today || [];
        return hourly.map((v, i) => ({
          label: i % 4 === 0 ? i + 'h' : '',
          value: v,
        }));
      }
      if (this.statRange === '30d') {
        const trend = this.stats.trend_30d || [];
        const now = Date.now();
        return trend.map((v, i) => {
          const d = new Date(now - (trend.length - 1 - i) * 86400000);
          const show = i % 5 === 0 || i === trend.length - 1;
          return { label: show ? String(d.getDate()) : '', value: v };
        });
      }
      const trend = this.stats.tokens?.trend || [0,0,0,0,0,0,0];
      const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
      return trend.map((v, i) => ({ label: days[i] || `Day ${i+1}`, value: v }));
    },
    perSessionSorted() {
      return (this.stats.per_session || [])
        .slice()
        .sort((x, y) => (y.input || 0) - (x.input || 0))
        .slice(0, 8);
    },
    weekdayLabels() {
      return [
        this.t('data.wdSun'), this.t('data.wdMon'), this.t('data.wdTue'),
        this.t('data.wdWed'), this.t('data.wdThu'), this.t('data.wdFri'),
        this.t('data.wdSat'),
      ];
    },
    activityMax() {
      return Math.max(...(this.stats.daily_activity || []).map(d => d.input || 0), 1);
    },
    activityHasData() {
      return (this.stats.daily_activity || []).some(d => (d.input || 0) > 0);
    },
    activityWeeks() {
      const list = this.stats.daily_activity || [];
      if (list.length === 0) return [];
      const byDate = {};
      list.forEach(d => { byDate[d.date] = d; });
      const weeks = [];
      let week = null;
      const cur = new Date(list[0].date + 'T00:00:00Z');
      const today = new Date();
      while (cur <= today) {
        const dow = cur.getUTCDay();
        if (dow === 0 || week === null) {
          week = new Array(7).fill(null);
          weeks.push(week);
        }
        const key = cur.toISOString().slice(0, 10);
        week[dow] = byDate[key] || { date: key, input: 0, requests: 0, cost_usd: 0 };
        cur.setUTCDate(cur.getUTCDate() + 1);
      }
      return weeks;
    },
    providerDonutData() {
      const palette = ['#2563eb','#2dd4a8','#ffd43b','#0b8f68','#e5484d','#60a5fa','#a87b00','#475467'];
      return (this.stats.per_provider || []).slice(0, 8).map((p, i) => ({
        label: p.provider,
        value: p.input,
        color: palette[i % palette.length],
      }));
    },
    cacheRateChartData() {
      if (this.statRange === 'today') {
        return (this.stats.trend_today_rate || []).map((v, i) => ({
          label: i % 4 === 0 ? i + 'h' : '',
          value: v,
        }));
      }
      if (this.statRange === '30d') {
        const trend = this.stats.cache_trend_30d || [];
        const now = Date.now();
        return trend.map((v, i) => {
          const d = new Date(now - (trend.length - 1 - i) * 86400000);
          const show = i % 5 === 0 || i === trend.length - 1;
          return { label: show ? String(d.getDate()) : '', value: v };
        });
      }
      const trend = this.stats.cache?.trend || [0,0,0,0,0,0,0];
      const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
      return trend.map((v, i) => ({ label: days[i] || `Day ${i+1}`, value: v }));
    },
    statRangeLabel() {
      const r = (this.statRanges || []).find(x => x.id === this.statRange);
      return r ? r.label : '';
    },
    filteredAgents() {
      const q = this.agentSearchQuery.toLowerCase().trim();
      if (!q) return this.agentList;
      return this.agentList.filter(a => a.name.toLowerCase().includes(q));
    },
    /** True when the editor holds edits that are not on disk yet. */
    agentDirty() {
      if (!this.selectedAgent) return false;
      return JSON.stringify(this.agentForm) !== this.agentSnapshot;
    },
    deleteAgentTooltip() {
      if (this.agentList.length <= 1) {
        return this.t('agents.lastAgent') || 'At least one agent must remain';
      }
      return this.t('agents.deleteAgent') || 'Delete agent';
    },
    /** "*" = every tool, including ones registered later. */
    isAllTools() {
      return (this.agentForm.zone2_tool_names || []).includes('*');
    },
    selectedToolCount() {
      return (this.agentForm.zone2_tool_names || []).filter(n => n && n !== '*').length;
    },
    filteredTools() {
      const q = this.toolSearch.trim().toLowerCase();
      if (!q) return this.toolList;
      return this.toolList.filter(t =>
        t.name.toLowerCase().includes(q) ||
        (t.description || '').toLowerCase().includes(q)
      );
    },
    groupedTools() {
      const groups = new Map();
      for (const tool of this.filteredTools) {
        const key = tool.extension || '__core';
        if (!groups.has(key)) {
          groups.set(key, {
            key,
            label: tool.extension || (this.t('agents.coreTools') || 'Core'),
            tools: [],
          });
        }
        groups.get(key).tools.push(tool);
      }
      return Array.from(groups.values());
    },
  },
  
  methods: {
    // I18nMixin updates currentLanguage; only the tab labels differ here.
    onLanguageChanged() {
      this.updateTabLabels();
    },
    
    switchDataTab(tabId) {
      this.activeTab = tabId;
      localStorage.setItem('boarai-tab-data', tabId);
      if (tabId === 'memoryManagement') this.loadMemoryData();
      if (tabId === 'recycleBin') this.loadTrash();
    },

    updateTabLabels() {
      this.tabs = [
        { id: 'ownerSettings', label: this.t('data.ownerSettings') || 'Owner Settings' },
        { id: 'roleManagement', label: this.t('data.roleManagement') || 'Role Management' },
        { id: 'memoryManagement', label: this.t('data.memoryManagement') || 'Memory Management' },
        { id: 'recycleBin', label: this.t('data.recycleBin') || 'Recycle Bin' },
        { id: 'usageStats', label: this.t('data.usageStats') || 'Usage Stats' }
      ];
    },
    
    async fetchStats() {
      try {
        const r = await fetch(`/api/monitoring/stats?range=${encodeURIComponent(this.statRange)}`);
        const body = await r.json();
        if (body && body.success && body.data) {
          this.stats = body.data;
        }
      } catch (error) {
        console.error('Failed to fetch stats:', error);
      }
    },

    async clearUsageStats() {
      const store = window.BoarAiStore;
      const message = this.t('data.clearStatsConfirm')
        || 'Permanently clear all token usage statistics? This cannot be undone.';
      const confirmed = store?.actions?.confirm
        ? await store.actions.confirm({
            title: this.t('data.clearStats') || 'Clear Stats',
            message,
            confirmText: this.t('data.clearStats') || 'Clear',
            cancelText: this.t('common.cancel') || 'Cancel',
            type: 'danger',
          })
        : confirm(message);
      if (!confirmed) return;
      try {
        const r = await fetch('/api/monitoring/stats/clear', { method: 'POST' });
        const body = await r.json().catch(() => ({}));
        if (!r.ok || body.success === false) {
          throw new Error((body.error && body.error.message) || String(r.status));
        }
        store?.actions?.success?.(this.t('data.statsCleared') || 'Usage statistics cleared');
        await this.fetchStats();
      } catch (e) {
        console.error('Clear usage stats failed:', e);
        store?.actions?.error?.(String(e.message || e));
      }
    },
    
    modelBarWidth(m) {
      const list = this.stats.per_model || [];
      const max = Math.max(...list.map(x => x.input || 0), 1);
      return Math.max(2, Math.round((m.input || 0) / max * 100));
    },

    sessionBarWidth(s) {
      const max = Math.max(...this.perSessionSorted.map(x => x.input || 0), 1);
      return Math.max(2, Math.round((s.input || 0) / max * 100));
    },

    activityLevel(input) {
      if (!input || input <= 0) return 0;
      const ratio = input / this.activityMax;
      if (ratio > 0.75) return 4;
      if (ratio > 0.5) return 3;
      if (ratio > 0.25) return 2;
      return 1;
    },

    onActivityHover(e, cell) {
      if (!cell) return;
      const wrap = this.$refs.activityCard;
      if (!wrap) return;
      const cr = e.target.getBoundingClientRect();
      const wr = wrap.getBoundingClientRect();
      let x = cr.left - wr.left + cr.width / 2 - 88;
      x = Math.min(Math.max(x, 4), wr.width - 180);
      const y = cr.top - wr.top - 86;
      this.activityHover = {
        date: cell.date,
        input: cell.input || 0,
        requests: cell.requests || 0,
        cost_usd: cell.cost_usd || 0,
        x,
        y: Math.max(y, 4),
      };
    },

    formatNumber(num) {
      return window.UiKit.formatNumber(num);
    },

    // ==================== Agent Management Methods ====================

    getEmptyForm() {
      return {
        name: '',
        avatar: 'bot',
        zone1_identity: '',
        zone1_rules: '',
        zone2_tool_names: ['*'],
        // Security (tool/path bounds only — approval is per-session)
        allowed_paths_text: '',
      };
    },

    formatDate(isoStr) {
      if (!isoStr) return '';
      try {
        const d = new Date(isoStr);
        const now = new Date();
        const diffMs = now - d;
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return this.t('common.justNow');
        if (diffMin < 60) return this.t('common.minAgo', { minutes: diffMin });
        return d.toLocaleDateString();
      } catch {
        return isoStr;
      }
    },

    formatDateTime(isoStr) {
      if (!isoStr) return '';
      try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return isoStr;
        const pad = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      } catch {
        return isoStr;
      }
    },

    // Map a pod URI to its on-disk file path (for display)
    memPodPath(podUri) {
      if (!podUri) return '';
      if (podUri === 'pod://global') return '.boarai/memory/global.json';
      const m = podUri.match(/^pod:\/\/([^/]+)\/(.+)$/);
      if (!m) return podUri;
      const kind = m[1];
      const id = m[2];
      switch (kind) {
        case 'session': return `.boarai/sessions/${id}/memory.json`;
        case 'agent': return `.boarai/agents/${id}/memory.json`;
        case 'shared': return `.boarai/memory/shared/${id}.json`;
        case 'skill': return `.boarai/memory/skills/${id}.json`;
        default: return podUri;
      }
    },

    async copyPodUri(entry) {
      const text = entry.pod_uri || '';
      try {
        await navigator.clipboard.writeText(text);
        window.BoarAiStore?.actions?.success(this.t('memory.podCopied') || 'Pod URI copied');
      } catch {
        window.prompt('Pod URI', text);
      }
    },

    async loadTools() {
      this.toolLoading = true;
      try {
        const res = await fetch('/api/tools');
        const result = await res.json();
        if (result.success) {
          this.toolList = (result.data && result.data.tools) || [];
        } else {
          console.error('Failed to load tools:', result.error);
        }
      } catch (e) {
        console.error('Failed to load tools:', e);
      } finally {
        this.toolLoading = false;
      }
    },

    isToolSelected(name) {
      const list = this.agentForm.zone2_tool_names || [];
      return list.includes('*') || list.includes(name);
    },

    toggleAllTools(checked) {
      this.agentForm.zone2_tool_names = checked ? ['*'] : [];
    },

    toggleTool(name, checked) {
      const list = (this.agentForm.zone2_tool_names || []).filter(n => n && n !== '*');
      const idx = list.indexOf(name);
      if (checked && idx < 0) list.push(name);
      if (!checked && idx >= 0) list.splice(idx, 1);
      this.agentForm.zone2_tool_names = list;
    },

    async loadAgents() {
      this.agentLoading = true;
      try {
        const res = await fetch('/api/agents');
        const result = await res.json();
        if (result.success) {
          this.agentList = result.data.agents || [];
        } else {
          console.error('Failed to load agents:', result.error);
          window.BoarAiStore?.actions?.error(this.t('notifications.loadFailed'));
        }
      } catch (e) {
        console.error('Failed to load agents:', e);
        window.BoarAiStore?.actions?.error(this.t('notifications.loadFailed'));
      } finally {
        this.agentLoading = false;
      }
    },

    selectAgent(agent) {
      this.isNewAgent = false;
      this.agentNameError = '';
      this.selectedAgent = agent;
      this.agentForm = {
        name: agent.name,
        avatar: agent.avatar || 'bot',
        zone1_identity: agent.zone1_identity || '',
        zone1_rules: agent.zone1_rules || '',
        zone2_tool_names: Array.isArray(agent.zone2_tool_names) ? [...agent.zone2_tool_names] : ['*'],
        allowed_paths_text: (agent.allowed_paths || []).join('\n'),
      };
      this.agentSnapshot = JSON.stringify(this.agentForm);
    },

    async createNewAgent() {
      if (!(await this.confirmDiscardIfDirty())) return;
      this.isNewAgent = true;
      this.agentNameError = '';
      this.selectedAgent = { name: '', is_default: false };
      this.agentForm = this.getEmptyForm();
      this.agentSnapshot = JSON.stringify(this.agentForm);
    },

    /**
     * Switching agents throws away the current form, so ask first when it has
     * unsaved edits. Returns false when the switch must be aborted.
     */
    async confirmDiscardIfDirty() {
      if (!this.agentDirty) return true;
      const discard = window.BoarAiStore?.actions?.confirm
        ? await window.BoarAiStore.actions.confirm({
            title: this.t('agents.unsavedTitle') || 'Unsaved changes',
            message: this.t('agents.unsavedDiscard') || 'Discard the unsaved changes to this agent?',
            confirmText: this.t('common.discard') || 'Discard',
            cancelText: this.t('common.cancel') || 'Cancel',
            type: 'warning',
          })
        : window.confirm(this.t('agents.unsavedDiscard') || 'Discard the unsaved changes to this agent?');
      return !!discard;
    },

    async onSelectAgent(agent) {
      if (!this.isNewAgent && agent.name === this.selectedAgent?.name) return;
      if (!(await this.confirmDiscardIfDirty())) return;
      this.selectAgent(agent);
    },

    async saveAgent() {
      if (this.agentSaving) return;
      const name = (this.agentForm.name || '').trim();
      this.agentNameError = '';
      if (!name) {
        this.agentNameError = this.t('agents.nameRequired');
        window.BoarAiStore?.actions?.warning(this.t('agents.nameRequired'));
        return;
      }
      if (!AGENT_NAME_RE.test(name)) {
        this.agentNameError = this.t('agents.nameInvalid') || 'Use letters, digits, "_" or "-" only';
        window.BoarAiStore?.actions?.warning(this.agentNameError);
        return;
      }
      if (this.isNewAgent && this.agentList.some(a => a.name === name)) {
        this.agentNameError = this.t('agents.nameExists') || 'An agent with this name already exists';
        window.BoarAiStore?.actions?.warning(this.agentNameError);
        return;
      }
      this.agentSaving = true;
      try {
        const payload = { ...this.agentForm, name };
        // The picker owns the tool array; an empty selection means "no tools",
        // which the UI warns about explicitly. Paths are one glob per line.
        payload.zone2_tool_names = (this.agentForm.zone2_tool_names || []).filter(Boolean);
        payload.allowed_paths = payload.allowed_paths_text
          ? payload.allowed_paths_text.split('\n').map(s => s.trim()).filter(Boolean)
          : [];
        delete payload.allowed_paths_text;

        const method = this.isNewAgent ? 'POST' : 'PUT';
        const url = this.isNewAgent ? '/api/agents' : `/api/agents/${encodeURIComponent(this.selectedAgent.name)}`;
        const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const result = await res.json();
        if (result.success) {
          window.BoarAiStore?.actions?.success(
            this.isNewAgent ? this.t('notifications.agentCreated') : this.t('notifications.agentUpdated')
          );
          // Reset new-agent flag so the next save is an update.
          this.isNewAgent = false;
          const savedName = (result.data && result.data.name) || name;
          await this.loadAgents();
          // Re-bind the selection to the freshly loaded record: without this
          // the editor kept the synthetic {name: ''} new-agent stub, so the
          // header title was blank and the next save PUT /api/agents/<empty>.
          const fresh = this.agentList.find(a => a.name === savedName);
          if (fresh) {
            this.selectAgent(fresh);
          } else {
            this.selectedAgent = result.data || { name: savedName, is_default: false };
            this.agentForm.name = savedName;
          }
        } else {
          window.BoarAiStore?.actions?.error(result.error?.message || this.t('notifications.saveFailed'));
        }
      } catch (e) {
        console.error('Save agent failed:', e);
        window.BoarAiStore?.actions?.error(this.t('notifications.saveFailed'));
      } finally {
        this.agentSaving = false;
      }
    },

    async deleteAgent(agent) {
      if (!agent?.name) {
        console.error('deleteAgent: agent name is empty', agent);
        window.BoarAiStore?.actions?.error(this.t('notifications.deleteFailed'));
        return;
      }
      // The backend refuses to delete the last remaining agent.
      if (this.agentList.length <= 1) {
        window.BoarAiStore?.actions?.warning(this.t('agents.lastAgent'));
        return;
      }

      const ask = (options) => (window.BoarAiStore?.actions?.confirm
        ? window.BoarAiStore.actions.confirm(options)
        : Promise.resolve(window.confirm(`${options.title}\n${options.message}`)));

      // Non-destructive question first: the final dialog must be the one that
      // can actually abort. (Asking "delete?" first and then "distill?" left
      // the user with no way out on the second dialog.)
      const distill = await ask({
        title: this.t('agents.distillTitle') || 'Extract memory?',
        message: this.t('agents.distillMessage', { name: agent.name }),
        confirmText: this.t('delete.distillOption') || 'Distill & Delete',
        cancelText: this.t('delete.normalOption') || 'Normal Delete',
        type: 'info',
      });

      const confirmed = await ask({
        title: this.t('agents.deleteConfirmTitle'),
        message: this.t('agents.deleteConfirmMessage', { name: agent.name }),
        confirmText: this.t('common.delete') || 'Delete',
        cancelText: this.t('common.cancel') || 'Cancel',
        type: 'danger',
      });
      if (!confirmed) return;

      try {
        const url = `/api/agents/${encodeURIComponent(agent.name)}${distill ? '?distill=true' : ''}`;
        const res = await fetch(url, { method: 'DELETE' });
        const result = await res.json();
        if (result.success) {
          const label = distill ? this.t('notifications.distillDeleteQueued') : this.t('notifications.agentDeleted');
          window.BoarAiStore?.actions?.success(label);
          if (this.selectedAgent?.name === agent.name) {
            this.selectedAgent = null;
            this.isNewAgent = false;
          }
          await this.loadAgents();
          // The backend hands the default flag to a remaining agent; select it
          // so the editor never sits on a stale/deleted record.
          const next = this.agentList.find(a => a.is_default) || this.agentList[0];
          if (!this.selectedAgent && next) this.selectAgent(next);
        } else {
          window.BoarAiStore?.actions?.error(result.error?.message || this.t('notifications.deleteFailed'));
        }
      } catch (e) {
        console.error('Delete agent failed:', e);
        window.BoarAiStore?.actions?.error(this.t('notifications.deleteFailed'));
      }
    },

    async setAsDefault(agent) {
      try {
        const res = await fetch(`/api/agents/${encodeURIComponent(agent.name)}/default`, { method: 'PUT' });
        const result = await res.json();
        if (result.success) {
          window.BoarAiStore?.actions?.success(this.t('notifications.defaultSet'));
          await this.loadAgents();
          // Re-bind so the "Default" badge and the star button reflect the new
          // state without a page reload (selectedAgent held a stale copy).
          const fresh = this.agentList.find(a => a.name === agent.name);
          if (fresh) this.selectAgent(fresh);
        } else {
          window.BoarAiStore?.actions?.error(result.error?.message || this.t('notifications.saveFailed'));
        }
      } catch (e) {
        console.error('Set default failed:', e);
        window.BoarAiStore?.actions?.error(this.t('notifications.saveFailed'));
      }
    },

    // ── User Profile ──

    async loadOwnerSettings() {
      try {
        const res = await fetch('/api/owner');
        const result = await res.json();
        if (result.success && result.data) {
          this.userContent = result.data.content || '';
          this.ownerLoadError = false;
        }
      } catch (e) {
        console.warn('Failed to load user profile:', e);
        this.ownerLoadError = true;
      }
    },

    async saveOwnerSettings() {
      this.ownerSaving = true;
      this.ownerSaved = false;
      try {
        const res = await fetch('/api/owner', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: this.userContent }),
        });
        const result = await res.json();
        if (result.success) {
          this.ownerSaved = true;
          setTimeout(() => { this.ownerSaved = false; }, 2000);
        } else {
          window.BoarAiStore?.actions?.error(result.error?.message || this.t('notifications.failedToSave'));
        }
      } catch (e) {
        console.error('Save user profile failed:', e);
        window.BoarAiStore?.actions?.error(this.t('notifications.saveOwnerFailed'));
      } finally {
        this.ownerSaving = false;
      }
    },

    // ── Memory Management ──

    async loadMemoryData() {
      this.memLoading = true;
      try {
        const store = window.BoarAiStore;
        if (store?.api?.getMemoryStats) {
          const stats = await store.api.getMemoryStats();
          if (stats) {
            this.memStats = {
              total_tags: stats.total_tags || 0,
              total_entries_indexed: stats.total_entries_indexed || 0,
              by_type: stats.entries_by_type || {},
            };
          }
        }
        if (store?.api?.getMemoryStaging) {
          const staging = await store.api.getMemoryStaging();
          const insights = staging?.insights || [];
          this.stagingList = insights;
          this.stagingCount = insights.filter(i => i.status !== 'confirmed').length;
          this.autoConfirmedCount = insights.filter(i => i.status === 'confirmed').length;
        }
        await this.searchMemory();
      } catch (e) {
        console.error('Load memory data failed:', e);
      } finally {
        this.memLoading = false;
      }
    },

    async searchMemory() {
      try {
        const store = window.BoarAiStore;
        if (store?.api?.getMemoryEntries) {
          const query = {};
          if (this.memSearch) query.text = this.memSearch;
          if (this.memTypeFilter) query.entry_type = this.memTypeFilter;
          query.limit = 50;
          const data = await store.api.getMemoryEntries(query);
          this.memEntries = data?.entries || [];
        }
      } catch (e) {
        console.error('Search memory failed:', e);
      }
    },

    // ---- Memory entry label helpers (i18n) ----

    memTypeLabel(type) {
      const map = {
        fact: '事实', decision: '决策', preference: '偏好', insight: '洞察',
        pattern: '模式', procedure: '流程', summary: '摘要', background: '背景',
        lesson: '教训',
      };
      return (this.currentLanguage === 'zh' ? map[type] : undefined) || type;
    },

    memTypeClass(type) {
      if (type === 'insight') return 'bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300';
      if (type === 'decision') return 'bg-sun-100 text-sun-700 dark:bg-sun-900/30 dark:text-sun-300';
      if (type === 'preference') return 'bg-mint-100 text-mint-700 dark:bg-mint-900/30 dark:text-mint-300';
      if (type === 'pattern') return 'bg-mint-100 text-mint-700 dark:bg-mint-900/30 dark:text-mint-300';
      if (type === 'lesson') return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300';
      if (type === 'procedure') return 'bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300';
      return 'bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300';
    },

    memTagLabel(tag) {
      const map = {
        compressed: '压缩', fact: '事实', decision: '决策', preference: '偏好',
        pattern: '模式', insight: '洞察', procedure: '流程', summary: '摘要',
        background: '背景', lesson: '教训', distilled: '蒸馏', recovered: '已恢复',
        preserved_from_session: '会话留存',
      };
      return (this.currentLanguage === 'zh' ? map[tag] : undefined) || tag;
    },

    // Show tags, excluding the one that duplicates the entry type badge
    memVisibleTags(entry) {
      const type = entry?.entry_type;
      return (entry?.tags || []).filter(t => t && t !== type).slice(0, 5);
    },

    memChannelLabel(ch) {
      const map = { webui: 'WebUI', cli: 'CLI', api: 'API', system: '系统' };
      return (this.currentLanguage === 'zh' ? map[ch] : undefined) || ch;
    },

    // ---- Memory entry edit / delete ----

    editMemoryEntry(entry) {
      this.memEditForm = {
        entry_id: entry.entry_id || '',
        pod_uri: entry.pod_uri || '',
        topic: entry.topic || '',
        content: entry.content || '',
        entry_type: entry.entry_type || 'fact',
        tags: (entry.tags || []).join(', '),
        importance: entry.importance ?? 0.5,
      };
      this.memEditVisible = true;
    },

    closeMemEdit() {
      this.memEditVisible = false;
    },

    async saveMemoryEntry() {
      const form = this.memEditForm;
      if (!form.entry_id) return;
      this.memEditSaving = true;
      try {
        const tags = String(form.tags || '').split(/[,，\s]+/).map(s => s.trim()).filter(Boolean);
        const fields = {
          pod: form.pod_uri || undefined,
          topic: form.topic.trim(),
          content: form.content.trim(),
          entry_type: form.entry_type,
          tags,
          importance: Math.max(0, Math.min(1, Number(form.importance) || 0)),
        };
        const result = await window.BoarAiStore.api.updateMemoryEntry(form.entry_id, fields);
        if (result.success) {
          window.BoarAiStore?.actions?.success(this.t('memory.updated') || 'Memory updated');
          this.memEditVisible = false;
          await this.loadMemoryData();
        } else {
          window.BoarAiStore?.actions?.error(result.error?.message || this.t('memory.saveFailed') || 'Update failed');
        }
      } catch (e) {
        console.error('Save memory entry failed:', e);
        window.BoarAiStore?.actions?.error(this.t('memory.saveFailed') || 'Update failed');
      } finally {
        this.memEditSaving = false;
      }
    },

    async deleteMemoryEntry(entry) {
      const contentPreview = (entry.content || '').substring(0, 50);
      const message = (this.t('memory.deleteConfirmMessage') || 'Delete this memory?').replace('{content}', contentPreview);
      const confirmed = window.BoarAiStore?.actions?.confirm
        ? await window.BoarAiStore.actions.confirm({
            title: this.t('memory.deleteTitle') || 'Delete Memory',
            message,
            confirmText: this.t('memory.delete') || 'Delete',
            cancelText: this.t('common.cancel') || 'Cancel',
            type: 'danger',
          })
        : confirm(message);
      if (!confirmed) return;
      try {
        const result = await window.BoarAiStore.api.deleteMemoryEntry(entry.entry_id, entry.pod_uri);
        if (result.success) {
          window.BoarAiStore?.actions?.success(this.t('memory.deleted') || 'Memory deleted');
          await this.loadMemoryData();
        } else {
          window.BoarAiStore?.actions?.error(result.error?.message || this.t('memory.deleteFailed') || 'Delete failed');
        }
      } catch (e) {
        console.error('Delete memory entry failed:', e);
        window.BoarAiStore?.actions?.error(this.t('memory.deleteFailed') || 'Delete failed');
      }
    },

    async doConsolidate() {
      try {
        const store = window.BoarAiStore;
        if (store?.api?.consolidateMemory) {
          await store.api.consolidateMemory();
          window.BoarAiStore?.actions?.success(this.t('notifications.consolidationStarted'));
          await this.loadMemoryData();
        }
      } catch (e) {
        console.error('Consolidate failed:', e);
      }
    },

    async confirmStagingItem(item) {
      try {
        const store = window.BoarAiStore;
        if (store?.api?.confirmStaging) {
          await store.api.confirmStaging(item.id);
          window.BoarAiStore?.actions?.success(this.t('notifications.confirmed'));
          await this.loadMemoryData();
        }
      } catch (e) {
        console.error('Confirm staging failed:', e);
      }
    },

    async rejectStagingItem(item) {
      try {
        const store = window.BoarAiStore;
        if (store?.api?.rejectStaging) {
          await store.api.rejectStaging(item.id);
          window.BoarAiStore?.actions?.success(this.t('notifications.rejected'));
          await this.loadMemoryData();
        }
      } catch (e) {
        console.error('Reject staging failed:', e);
      }
    },

    // ── Recycle Bin ──

    async loadTrash() {
      this.trashLoading = true;
      try {
        const store = window.BoarAiStore;
        if (store?.api?.getTrash) {
          const data = await store.api.getTrash(this.trashFilter || undefined);
          this.trashList = data?.entries || [];
          this.trashStats = data?.stats || { total: 0, by_type: {} };
        }
      } catch (e) {
        console.error('Load trash failed:', e);
      } finally {
        this.trashLoading = false;
      }
    },

    async restoreTrashItem(item) {
      try {
        const store = window.BoarAiStore;
        if (store?.api?.restoreTrash) {
          const result = await store.api.restoreTrash(item.trash_id);
          if (result.success) {
            window.BoarAiStore?.actions?.success(this.t('notifications.restored'));
            await this.loadTrash();
          } else {
            window.BoarAiStore?.actions?.error(result.error?.message || this.t('notifications.restoreFailed'));
          }
        }
      } catch (e) {
        console.error('Restore failed:', e);
      }
    },

    async purgeTrashItem(item) {
      const confirmed = window.BoarAiStore?.actions?.confirm
        ? await window.BoarAiStore.actions.confirm({
            title: this.t('recycleBin.purgeTitle') || 'Permanent Delete',
            message: this.t('recycleBin.purgeConfirmMessage', { name: this.trashItemName(item) }) || `Permanently delete "${this.trashItemName(item)}"? This action cannot be undone.`,
            confirmText: this.t('recycleBin.purge') || 'Permanent Delete',
            cancelText: this.t('common.cancel') || 'Cancel',
            type: 'danger',
          })
        : confirm(this.t('recycleBin.purgeConfirmMessage', { name: this.trashItemName(item) }) || `Permanently delete "${this.trashItemName(item)}"? This action cannot be undone.`);
      if (!confirmed) return;
      try {
        const store = window.BoarAiStore;
        if (store?.api?.purgeTrashItem) {
          await store.api.purgeTrashItem(item.trash_id);
          window.BoarAiStore?.actions?.success(this.t('recycleBin.purged') || 'Purged');
          await this.loadTrash();
        }
      } catch (e) {
        console.error('Purge failed:', e);
      }
    },

    async emptyAllTrash() {
      const confirmed = window.BoarAiStore?.actions?.confirm
        ? await window.BoarAiStore.actions.confirm({
            title: this.t('recycleBin.purgeAllTitle') || 'Empty Recycle Bin',
            message: this.t('recycleBin.purgeAllConfirmMessage') || 'Permanently delete ALL trash entries? This action cannot be undone.',
            confirmText: this.t('recycleBin.purgeAll') || 'Empty Recycle Bin',
            cancelText: this.t('common.cancel') || 'Cancel',
            type: 'danger',
          })
        : confirm(this.t('recycleBin.purgeAllConfirmMessage') || 'Permanently delete ALL trash entries? This action cannot be undone.');
      if (!confirmed) return;
      try {
        const store = window.BoarAiStore;
        if (store?.api?.purgeAllTrash) {
          await store.api.purgeAllTrash();
          window.BoarAiStore?.actions?.success(this.t('recycleBin.allPurged') || 'All trash cleared');
          await this.loadTrash();
        }
      } catch (e) {
        console.error('Empty trash failed:', e);
      }
    },

    async distillTrashItem(item) {
      try {
        const store = window.BoarAiStore;
        if (store?.api?.distillTrashItem) {
          const result = await store.api.distillTrashItem(item.trash_id);
          if (result.success) {
            window.BoarAiStore?.actions?.success(this.t('notifications.distillationQueued'));
            await this.loadTrash();
          } else {
            window.BoarAiStore?.actions?.error(result.error?.message || this.t('notifications.distillFailed'));
          }
        }
      } catch (e) {
        console.error('Distill failed:', e);
      }
    },

    trashItemTitle(item) {
      if (item.type === 'memory') return item.metadata?.topic || item.metadata?.content_preview || item.trash_id;
      if (item.type === 'session') return item.metadata?.title || item.metadata?.session_id || item.original_path?.split('/').pop() || item.trash_id;
      if (item.type === 'agent') return item.original_path?.split('/').pop() || item.trash_id;
      if (item.type === 'file') return item.original_path?.split('/').pop()?.split('__').pop() || item.trash_id;
      return item.trash_id;
    },

    trashItemId(item) {
      if (item.type === 'session') return item.metadata?.session_id || '';
      if (item.type === 'memory') return item.metadata?.entry_id || '';
      return '';
    },

    trashItemName(item) {
      return this.trashItemTitle(item);
    },

    typeLabel(type) {
      const map = { session: 'Session', agent: 'Agent', file: 'File', memory: 'Memory' };
      return this.t('recycleBin.type' + type.charAt(0).toUpperCase() + type.slice(1)) || map[type] || type;
    },

    typeBadgeClass(type) {
      const cls = {
        session: 'bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300',
        agent: 'bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300',
        file: 'bg-sun-100 text-sun-700 dark:bg-sun-900/30 dark:text-sun-300',
        memory: 'bg-mint-100 text-mint-700 dark:bg-mint-900/30 dark:text-mint-300',
      };
      return cls[type] || 'bg-gray-100 text-gray-700';
    },

    formatTrashTime(iso) {
      try {
        const d = new Date(iso);
        const now = new Date();
        const diff = now - d;
        if (diff < 60000) return 'Just now';
        if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
        if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
        return Math.floor(diff / 86400000) + 'd ago';
      } catch (e) { return ''; }
    },
  }
};
