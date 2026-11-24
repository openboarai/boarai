/**
 * Settings Page Component
 * 
 * Application settings view with secondary navigation.
 * Uses TailwindCSS for styling.
 * Supports internationalization (i18n).
 * Uses Lucide Icons for all icons.
 */

const SettingsPage = {
  name: 'SettingsPage',
  
  template: `
    <div class="max-w-7xl mx-auto px-4 py-6 animate-fade-in">
      <!-- Secondary Navigation -->
      <div class="mb-6">
        <tab-bar :model-value="activeTab" :tabs="tabs"
                 @update:model-value="switchSettingsTab"></tab-bar>
      </div>

      <!-- Tab Content -->
      <div class="space-y-4">
        
        <!-- Providers Tab -->
        <div v-if="activeTab === 'providers'" class="space-y-4">

          <card-section icon="database" :title="t('settings.providers') || 'Providers'" body-class="p-3 sm:p-4">
            <template #header-actions>
              <base-button size="sm" icon="plus" @click="openAddProvider">
                {{ t('settings.addProvider') || 'Add Provider' }}
              </base-button>
            </template>
            <!-- Three columns: provider list | draft model list | provider or
                 model settings (replaces the former modal). Phones stack the
                 columns and scroll the provider list sideways. -->
            <div class="flex flex-col lg:flex-row gap-3 sm:gap-4">
              <!-- Column 1: provider list -->
              <div class="w-full lg:w-56 xl:w-64 shrink-0">
                <div v-if="providers.length === 0"
                     class="px-3 py-6 text-center border border-dashed border-gray-200 dark:border-gray-700 rounded-lg">
                  <p class="text-sm text-gray-500 dark:text-gray-500">{{ t('settings.noProviders') || 'No providers configured yet' }}</p>
                  <p class="text-xs text-gray-400 dark:text-gray-500 mt-1">{{ t('settings.addProviderHint') || 'Click "Add Provider" to get started' }}</p>
                </div>
                <nav v-else class="flex gap-1.5 overflow-x-auto pb-1 lg:flex-col lg:gap-1 lg:overflow-visible lg:pb-0">
                  <!-- Placeholder entry while the new-provider form is open -->
                  <div v-if="providerFormMode === 'new'"
                       class="shrink-0 w-52 lg:w-full text-left px-3 py-2 rounded-lg border border-primary/60 bg-primary/5">
                    <div class="flex items-center gap-2 min-w-0">
                      <i data-lucide="plus" class="w-3.5 h-3.5 text-primary shrink-0"></i>
                      <span class="text-sm font-medium text-primary truncate">{{ t('settings.newProvider') || 'New Provider' }}</span>
                    </div>
                    <div class="text-xs text-gray-500 dark:text-gray-500 truncate mt-0.5">{{ t('settings.newProviderPending') || 'Not saved yet' }}</div>
                  </div>
                  <button v-for="provider in providers" :key="provider.name" type="button"
                          @click="selectProvider(provider)"
                          class="shrink-0 w-52 lg:w-full text-left px-3 py-2 rounded-lg border transition-colors"
                          :class="isProviderSelected(provider.name)
                            ? 'border-primary/60 bg-primary/5'
                            : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700'">
                    <div class="flex items-center gap-2 min-w-0">
                      <span class="text-sm font-medium truncate"
                            :class="isProviderSelected(provider.name) ? 'text-primary' : 'text-gray-900 dark:text-white'">{{ provider.name }}</span>
                      <span class="ml-auto shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-500">
                        {{ provider.provider_type || (provider.api_base?.includes('anthropic') ? 'anthropic' : 'openai') }}
                      </span>
                    </div>
                    <div class="text-xs text-gray-500 dark:text-gray-500 truncate mt-0.5" :title="provider.api_base">{{ provider.api_base }}</div>
                    <div class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                      {{ t('settings.providerModelCount', { count: (provider.supported_models || []).length }) || ((provider.supported_models || []).length + ' models') }}
                    </div>
                  </button>
                </nav>
              </div>

              <!-- Nothing selected yet: one wide hint instead of two empty columns -->
              <div v-if="!providerFormMode"
                   class="flex-1 min-w-0 py-10 text-center border-t border-gray-100 dark:border-gray-700 pt-3 lg:border-t-0 lg:border-l lg:pl-4">
                <i data-lucide="hard-drive" class="w-8 h-8 mx-auto text-gray-300 dark:text-gray-600"></i>
                <p class="text-sm text-gray-500 dark:text-gray-500 mt-2">{{ t('settings.providerDetailHint') || 'Select a provider on the left to view its settings' }}</p>
              </div>
              <template v-else>
              <!-- Column 2: the draft model list (moved out of the form so the
                   card stays short; bounded height with its own scroll) -->
              <div class="w-full lg:w-64 xl:w-80 shrink-0 border-t border-gray-100 dark:border-gray-700 pt-3 lg:border-t-0 lg:border-l lg:pl-3 xl:pl-4">
                <div class="flex items-center justify-between gap-2 mb-2">
                  <span class="text-xs font-medium text-gray-600 dark:text-gray-300 truncate">
                    {{ t('settings.supportedModels') || 'Supported Models' }}
                    <span class="text-gray-400 dark:text-gray-500">({{ (form.selected_models || []).length }})</span>
                  </span>
                  <button @click="fetchModels"
                          :disabled="form.fetching_models || !form.api_base || (!editingProvider && !form.api_key)"
                          :title="t('settings.fetchModels') || 'Fetch Models'"
                          class="shrink-0 p-1 rounded text-primary hover:bg-primary/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                    <svg v-if="form.fetching_models" xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                    <svg v-else xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-9-9"/><path d="M21 3v6h-6"/></svg>
                  </button>
                </div>
                <div v-if="(form.selected_models || []).length === 0"
                     class="px-2 py-4 text-center text-xs text-gray-500 bg-gray-50 dark:bg-gray-700/50 border border-dashed border-gray-200 dark:border-gray-600 rounded-lg">
                  {{ t('settings.noModelsYet') || 'No models yet — fetch from API or add manually' }}
                </div>
                <!-- Single-line rows; clicking one opens its settings in column 3 -->
                <div v-else class="space-y-1 max-h-64 overflow-y-auto pr-0.5">
                  <div v-for="m in form.selected_models" :key="'sel-' + m"
                       role="button" tabindex="0"
                       @click="selectModelDetail(m)"
                       @keyup.enter="selectModelDetail(m)"
                       @keyup.space.prevent="selectModelDetail(m)"
                       :title="t('settings.modelListHint') || 'Click a model to edit its capabilities'"
                       class="flex items-center gap-1.5 px-2 py-1.5 rounded-md border cursor-pointer transition-colors focus:outline-none focus:ring-2 focus:ring-primary/50"
                       :class="modelDetail === m && detailMode === 'model'
                         ? 'border-primary/60 bg-primary/5'
                         : 'border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 hover:bg-gray-50 dark:hover:bg-gray-600'">
                    <span class="flex-1 min-w-0 text-xs truncate"
                          :class="modelDetail === m && detailMode === 'model' ? 'text-primary font-medium' : 'text-gray-800 dark:text-gray-200'">{{ m }}</span>
                    <!-- Enabled capability icons: glanceable without opening the panel -->
                    <span v-if="enabledCaps(m).length"
                          class="flex items-center gap-0.5 shrink-0 text-primary/80 dark:text-primary/90"
                          :title="enabledCapsTitle(m)">
                      <i v-for="mc in enabledCaps(m)" :key="mc.key" :data-lucide="mc.icon" class="w-3 h-3"></i>
                    </span>
                    <span class="shrink-0 text-[10px] px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-750 text-gray-500 dark:text-gray-400">{{ ctxBadge(m) }}</span>
                    <button type="button" @click.stop="removeModel(m)"
                            class="shrink-0 p-0.5 rounded hover:bg-red-50 dark:hover:bg-red-900/20 text-gray-400 hover:text-red-500 transition-colors"
                            :title="t('common.delete') || 'Remove'">
                      <i data-lucide="x" class="w-3.5 h-3.5"></i>
                    </button>
                  </div>
                </div>
                <!-- Manual add (works without API fetch) -->
                <div class="mt-2 flex items-center gap-1.5">
                  <input v-model="form.manual_model" type="text" @keyup.enter="addManualModel"
                         class="flex-1 min-w-0 px-2 py-1.5 text-xs bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50"
                         :placeholder="t('settings.manualModelPlaceholder') || 'Model id, e.g. gpt-4o'">
                  <button type="button" @click="addManualModel"
                          :disabled="!(form.manual_model || '').trim()"
                          :title="t('settings.addModel') || 'Add'"
                          class="shrink-0 p-1.5 rounded-md bg-primary/10 text-primary border border-primary/20 hover:bg-primary/15 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                    <i data-lucide="plus" class="w-3.5 h-3.5"></i>
                  </button>
                </div>
                <!-- Fetched from API -->
                <div v-if="form.fetching_models"
                     class="mt-2 px-2 py-3 text-center text-xs text-gray-500 bg-gray-50 dark:bg-gray-700 border border-dashed border-gray-200 dark:border-gray-600 rounded-lg">
                  {{ t('common.loading') || 'Loading...' }}
                </div>
                <div v-else-if="form.fetched_models.length > 0"
                     class="mt-2 max-h-40 overflow-y-auto bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg p-1.5 space-y-0.5">
                  <div class="text-[11px] text-gray-500 dark:text-gray-500 px-1 pb-1">
                    {{ t('settings.fetchedModels') || 'Fetched from API' }}
                    <span class="text-gray-500">({{ form.fetched_models.length }})</span>
                  </div>
                  <label v-for="m in form.fetched_models" :key="m.id"
                         class="flex items-center gap-1.5 px-1.5 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-600 cursor-pointer">
                    <input type="checkbox"
                           :checked="(form.selected_models || []).includes(m.id)"
                           @change="toggleFetchedModel(m.id)"
                           class="w-3.5 h-3.5 text-primary rounded focus:ring-primary/50">
                    <span class="text-xs text-gray-700 dark:text-gray-300 truncate">{{ m.name || m.id }}</span>
                  </label>
                </div>
              </div>

              <!-- Column 3: provider settings, or the clicked model's settings.
                   Fixed desktop height + bottom-pinned footer so switching
                   provider/model panels never changes the card height. -->
              <div class="flex-1 min-w-0 border-t border-gray-100 dark:border-gray-700 pt-3 lg:border-t-0 lg:border-l lg:pl-4 lg:flex lg:flex-col lg:h-[520px]">
                <div ref="providerDetail" class="space-y-4 flex-1 min-h-0 flex flex-col lg:overflow-y-auto">
                  <!-- Model settings panel (opened by clicking a model row) -->
                  <template v-if="detailMode === 'model' && modelDetail">
                    <div class="flex items-start justify-between gap-3 pb-3 border-b border-gray-100 dark:border-gray-700">
                      <div class="min-w-0">
                        <h4 class="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white min-w-0">
                          <i data-lucide="box" class="w-4 h-4 text-primary shrink-0"></i>
                          <span class="truncate">{{ modelDetail }}</span>
                        </h4>
                        <p class="text-xs text-gray-500 dark:text-gray-500 mt-1">
                          {{ t('settings.modelSettingsHint') || 'Context length and capability toggles for this model' }}
                        </p>
                      </div>
                      <button @click="backToProvider"
                              class="shrink-0 inline-flex items-center gap-1 px-2 py-1 text-xs text-primary hover:bg-primary/5 rounded-md transition-colors">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
                        {{ t('settings.backToProvider') || 'Back to provider settings' }}
                      </button>
                    </div>
                    <!-- Context length -->
                    <div>
                      <label class="text-xs font-medium text-gray-700 dark:text-gray-300 block mb-1.5">{{ t('settings.contextLength') || 'Context Length' }}</label>
                      <div class="flex items-center gap-2">
                        <select :value="ctxMode(modelDetail)" @change="onCtxModeChange(modelDetail, $event.target.value)"
                                class="px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50">
                          <option value="">{{ ctxDefaultLabel(modelDetail) }}</option>
                          <option value="128000">128k</option>
                          <option value="512000">512k</option>
                          <option value="1000000">1m</option>
                          <option value="custom">{{ t('settings.contextLengthCustom') || 'Custom' }}</option>
                        </select>
                        <input v-if="ctxMode(modelDetail) === 'custom'" type="number" min="1024" step="1024"
                               :value="form.model_context_windows[modelDetail] || ''"
                               @input="onCtxCustomInput(modelDetail, $event.target.value)"
                               class="w-28 px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50"
                               placeholder="tokens">
                      </div>
                    </div>
                    <!-- Capability toggles, persisted via the shared Save button -->
                    <div>
                      <div class="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">{{ t('settings.modelCapsTitle') || 'Supported capabilities' }}</div>
                      <div class="space-y-1.5">
                        <div v-for="mc in modelCaps" :key="mc.key"
                             class="flex items-center justify-between gap-3 rounded-lg border border-gray-100 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-750/40 px-3 py-2">
                          <span class="flex items-center gap-2 min-w-0 text-sm text-gray-700 dark:text-gray-300">
                            <i :data-lucide="mc.icon" class="w-4 h-4 text-primary shrink-0"></i>
                            <span class="truncate">{{ t('settings.mcap_' + mc.key) || mc.label }}</span>
                          </span>
                          <toggle-switch :model-value="getModelCap(modelDetail, mc.key)"
                                         @update:model-value="setModelCap(modelDetail, mc.key, $event)"></toggle-switch>
                        </div>
                      </div>
                    </div>
                  </template>
                  <!-- Provider settings panel -->
                  <template v-else>
                  <!-- Panel header + provider-level actions -->
                  <div class="flex items-start justify-between gap-3 pb-3 border-b border-gray-100 dark:border-gray-700">
                    <div class="min-w-0">
                      <h4 class="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white min-w-0">
                        <i :data-lucide="providerFormMode === 'edit' ? 'pencil' : 'plus'" class="w-4 h-4 text-primary shrink-0"></i>
                        <span class="truncate">{{ providerFormMode === 'edit' ? (editingProvider?.name || (t('settings.editProvider') || 'Edit Provider')) : (t('settings.newProvider') || 'New Provider') }}</span>
                      </h4>
                      <p class="text-xs text-gray-500 dark:text-gray-500 mt-1">
                        {{ providerFormMode === 'edit' ? (t('settings.editProviderHint') || 'Click Save to apply your changes') : (t('settings.newProviderDesc') || 'Fill in the details below, then save to create the provider') }}
                      </p>
                    </div>
                    <div v-if="providerFormMode === 'edit'" class="flex items-center gap-1 shrink-0">
                      <button @click="validateProvider(editingProvider.name)"
                              class="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 hover:text-green-500 transition-colors"
                              :title="t('settings.validateProvider') || 'Validate'">
                        <i data-lucide="check-circle" class="w-4 h-4"></i>
                      </button>
                      <button @click="deleteProvider(editingProvider.name)"
                              class="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 hover:text-red-500 transition-colors"
                              :title="t('common.delete') || 'Delete'">
                        <i data-lucide="trash-2" class="w-4 h-4"></i>
                      </button>
                    </div>
                  </div>
                  <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <label class="text-xs font-medium text-gray-700 dark:text-gray-300 block mb-1.5">{{ t('settings.providerName') || 'Name' }}</label>
                      <input v-model="form.name" type="text" :disabled="providerFormMode === 'edit'"
                             class="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50"
                             :placeholder="'openai'">
                    </div>
                    <div>
                      <label class="text-xs font-medium text-gray-700 dark:text-gray-300 block mb-1.5">{{ t('settings.providerType') || 'Type' }}</label>
                      <select v-model="form.provider_type" :disabled="providerFormMode === 'edit'"
                              class="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                        <option value="openai">{{ t('settings.providerTypeOpenai') }}</option>
                        <option value="anthropic">{{ t('settings.providerTypeAnthropic') }}</option>
                      </select>
                    </div>
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-700 dark:text-gray-300 block mb-1.5">{{ t('settings.apiKey') }}</label>
                    <input v-model="form.api_key" type="password"
                           class="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50"
                           :placeholder="'sk-...'">
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-700 dark:text-gray-300 block mb-1.5">{{ t('settings.apiBaseUrl') }}</label>
                    <input v-model="form.api_base" type="text"
                           class="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50"
                           :placeholder="form.provider_type === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'">
                  </div>
                  </template>
                  <!-- Absorbs leftover height so the footer sits at the same
                       place in both panel modes (space-y beats mt-auto). -->
                  <div class="flex-1"></div>
                  <!-- Form footer -->
                  <div class="flex items-center justify-end gap-3 pt-3 border-t border-gray-100 dark:border-gray-700">
                    <base-button variant="secondary" :disabled="savingProvider" @click="cancelProviderForm">
                      {{ t('common.cancel') || 'Cancel' }}
                    </base-button>
                    <base-button variant="primary" icon="check" :loading="savingProvider" @click="saveProvider">
                      {{ providerFormMode === 'edit' ? (t('common.save') || 'Save') : (t('common.add') || 'Add') }}
                    </base-button>
                  </div>
                </div>
              </div>
              </template>
            </div>
          </card-section>

          <!-- Capability model assignment: one compact tile per role so all five
               stay visible at a glance while the card takes a single row of
               space. Each tile drills down provider → model in-place. -->
          <div v-if="providers.length > 0"
               class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700">
            <div class="px-4 py-2 bg-gray-50 dark:bg-gray-750 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between gap-3">
              <h3 class="font-semibold text-gray-900 dark:text-white flex items-center gap-2 text-sm min-w-0">
                <i data-lucide="cpu" class="w-4 h-4 text-primary shrink-0"></i>
                <span class="truncate">{{ t('settings.capGroupTitle') || 'Model Assignment' }}</span>
              </h3>
              <span class="text-xs text-gray-500 shrink-0 hidden sm:inline">{{ t('settings.capOptional') || 'optional, falls back to chat model' }}</span>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2 p-3">
              <div v-for="cap in capabilities" :key="cap.key"
                   class="relative min-w-0 rounded-lg border border-gray-100 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-750/40 px-2.5 py-2"
                   :ref="'capDropdown_' + cap.key">
                <!-- Role label + save slot -->
                <div class="flex items-center justify-between gap-1.5 mb-1.5">
                  <span class="flex items-center gap-1.5 min-w-0 text-xs text-gray-600 dark:text-gray-300"
                        :title="t('settings.cap_' + cap.key) || cap.label">
                    <i :data-lucide="cap.icon" class="w-3.5 h-3.5 text-primary shrink-0"></i>
                    <span class="truncate">{{ t('settings.cap_' + cap.key) || cap.label }}</span>
                  </span>
                  <button v-if="capDirty(cap.key)" @click="saveCapabilityModel(cap.key)"
                          :disabled="getCapState(cap.key).saving"
                          :title="t('common.save') || 'Save'"
                          class="shrink-0 p-1 rounded text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                    <svg v-if="getCapState(cap.key).saving" xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                    <svg v-else xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/></svg>
                  </button>
                  <span v-else-if="capSaved(cap.key)" class="shrink-0 inline-flex items-center text-emerald-600 dark:text-emerald-400"
                        :title="t('settings.capSaved') || 'Saved'">
                    <i data-lucide="check" class="w-3.5 h-3.5"></i>
                  </span>
                </div>
                <!-- Compact drill-down trigger -->
                <button @click="capToggle(cap.key)"
                        class="w-full min-h-[30px] flex items-center justify-between gap-1.5 px-2 py-1 text-xs bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 hover:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors">
                  <span v-if="getCapState(cap.key).provider && getCapState(cap.key).model" class="flex items-center gap-1 min-w-0">
                    <span class="text-gray-900 dark:text-white font-medium truncate">{{ getCapState(cap.key).provider }}</span>
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5 text-gray-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>
                    <span class="text-primary font-medium truncate">{{ getCapState(cap.key).model }}</span>
                  </span>
                  <span v-else class="text-gray-400 dark:text-gray-500 truncate">{{ t('settings.selectProviderAndModel') || 'Select provider & model...' }}</span>
                  <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 text-gray-500 shrink-0 transition-transform" :class="{ 'rotate-180': getCapState(cap.key).open }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>
                </button>
                <!-- Drill-down menu: providers first, then that provider's models.
                     Flips above the tile when the viewport has no room below. -->
                <div v-if="getCapState(cap.key).open"
                     class="absolute left-0 w-full bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl z-50 overflow-hidden"
                     :class="getCapState(cap.key).dropUp ? 'bottom-full mb-1' : 'top-full mt-1'">
                  <button v-if="getCapState(cap.key).level === 'model'" @click="capBack(cap.key)"
                          class="w-full flex items-center gap-1 px-2.5 py-1.5 text-xs text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-700 border-b border-gray-100 dark:border-gray-700">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
                    <span class="truncate">{{ getCapState(cap.key).provider }}</span>
                  </button>
                  <div v-if="getCapState(cap.key).level !== 'model'" class="max-h-56 overflow-y-auto py-1">
                    <button v-for="p in providers" :key="p.name"
                            @click="capSelectProvider(cap.key, p)"
                            class="w-full flex items-center justify-between gap-1 px-2.5 py-1.5 text-xs text-left hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                            :class="getCapState(cap.key).provider === p.name ? 'bg-primary/5 text-primary font-medium' : 'text-gray-700 dark:text-gray-300'">
                      <span class="truncate">{{ p.name }}</span>
                      <svg v-if="(p.supported_models || []).length > 0" xmlns="http://www.w3.org/2000/svg" class="w-3 h-3 text-gray-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>
                    </button>
                  </div>
                  <div v-else class="max-h-56 overflow-y-auto py-1">
                    <div v-if="getCapState(cap.key).models.length === 0" class="px-2.5 py-4 text-center text-xs text-gray-500">
                      {{ t('settings.noModelsAvailable') || 'No models available' }}
                    </div>
                    <button v-for="m in getCapState(cap.key).models" :key="m"
                            @click="capSelectModel(cap.key, m)"
                            class="w-full px-2.5 py-1.5 text-xs text-left text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors truncate"
                            :class="getCapState(cap.key).model === m ? 'bg-primary/5 text-primary font-medium' : ''">
                      {{ m }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>

        </div>

        <!-- Appearance Tab -->
        <div v-if="activeTab === 'appearance'" class="space-y-4">
          <card-section icon="palette" :title="t('settings.appearance')">
            <div class="p-4 space-y-4">
              <div class="flex items-center justify-between">
                <div>
                  <div class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.theme') }}</div>
                  <div class="text-xs text-gray-500 dark:text-gray-500">{{ t('settings.chooseTheme') }}</div>
                </div>
                <select v-model="theme" @change="handleThemeChange" 
                        class="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                  <option value="light">{{ t('common.light') }}</option>
                  <option value="dark">{{ t('common.dark') }}</option>
                  <option value="system">{{ t('common.system') }}</option>
                </select>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <div class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.colorTheme') }}</div>
                  <div class="text-xs text-gray-500 dark:text-gray-500">{{ t('settings.chooseColorTheme') }}</div>
                </div>
                <div class="flex items-center gap-2">
                  <button v-for="ct in colorThemes" :key="ct.code"
                          @click="setColorTheme(ct.code)"
                          class="w-8 h-8 rounded-full border-2 transition-all duration-200 hover:scale-110"
                          :class="colorTheme === ct.code ? 'border-gray-900 dark:border-white scale-110' : 'border-transparent'"
                          :style="{ backgroundColor: ct.hex }"
                          :title="ct.name">
                  </button>
                </div>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <div class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.language') }}</div>
                  <div class="text-xs text-gray-500 dark:text-gray-500">{{ t('settings.selectLanguage') }}</div>
                </div>
                <select v-model="language" @change="applyLanguage"
                        class="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                  <option value="en">{{ t('common.english') }}</option>
                  <option value="zh">{{ t('common.chinese') }}</option>
                </select>
              </div>
              <!-- Homepage Setting -->
              <div class="flex items-center justify-between">
                <div>
                  <div class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.homepage') }}</div>
                  <div class="text-xs text-gray-500 dark:text-gray-500">{{ t('settings.homepageDesc') }}</div>
                </div>
                <select v-model="homepage" @change="handleHomepageChange"
                        class="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                  <option value="dashboard">{{ t('nav.home') }}</option>
                  <option value="sessions">{{ t('nav.chat') }}</option>
                  <option value="tasks">{{ t('nav.tasks') }}</option>
                  <option value="extensions">{{ t('nav.extensions') }}</option>
                  <option value="data">{{ t('nav.data') }}</option>
                  <option value="settings">{{ t('nav.settings') }}</option>
                </select>
              </div>
            </div>
          </card-section>
        </div>

        <!-- General Tab -->
        <div v-if="activeTab === 'general'" class="space-y-4">
          <card-section icon="settings-2" :title="t('settings.general') || 'General'">
            <div class="space-y-4">
              <div>
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300 block mb-1.5">{{ t('settings.serverUrl') }}</label>
                <input type="text" value="http://localhost:26620" 
                       class="w-full px-3 py-2 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary">
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <div class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.autoReconnect') }}</div>
                  <div class="text-xs text-gray-500 dark:text-gray-500">{{ t('settings.autoReconnectDesc') }}</div>
                </div>
                <toggle-switch :model-value="true" disabled size="lg"></toggle-switch>
              </div>
            </div>
          </card-section>
        </div>

        <!-- Sessions Tab -->
        <div v-if="activeTab === 'sessions'" class="space-y-4">
          <!-- Context / compression -->
          <card-section icon="gauge" :title="t('settings.compressionThreshold') || 'Compression Threshold'" body-class="p-4">
            <template #title>
              {{ t('settings.compressionThreshold') || 'Compression Threshold' }}
              <p class="text-xs text-gray-500 dark:text-gray-500 mt-1 ml-6 font-normal">
                {{ t('settings.compressionThresholdDesc') || 'Auto-compress when context usage exceeds this ratio' }}
              </p>
            </template>
            <div class="flex items-center gap-4">
              <div class="flex-1 min-w-0">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-xs text-gray-500 tabular-nums">50%</span>
                  <span class="text-sm font-semibold tabular-nums text-primary bg-primary/10 px-2.5 py-0.5 rounded-full">
                    {{ compressionThreshold }}%
                  </span>
                  <span class="text-xs text-gray-500 tabular-nums">80%</span>
                </div>
                <input type="range" min="50" max="80" step="5" v-model.number="compressionThreshold"
                       @change="handleCompressionThresholdChange"
                       class="w-full h-2 bg-gray-200 dark:bg-gray-600 rounded-lg appearance-none cursor-pointer accent-primary">
              </div>
            </div>
          </card-section>

          <!-- Default workspace -->
          <card-section icon="folder-tree" :title="t('settings.defaultWorkspace') || 'Default Workspace Directory'" body-class="p-4 space-y-3">
            <template #title>
              {{ t('settings.defaultWorkspace') || 'Default Workspace Directory' }}
              <p class="text-xs text-gray-500 dark:text-gray-500 mt-1 ml-6 font-normal">
                {{ t('settings.defaultWorkspaceDesc') || 'Default working directory for new sessions' }}
              </p>
            </template>
            <template #header-actions>
              <span v-if="workspaceSettingSaving"
                    class="text-xs text-gray-500 dark:text-gray-500 whitespace-nowrap">
                {{ t('common.loading') || 'Saving...' }}
              </span>
            </template>
            <div class="space-y-3">
              <radio-group :model-value="defaultWorkspaceMode" layout="card"
                           :options="workspaceModeOptions"
                           @update:model-value="setWorkspaceMode($event)"></radio-group>

              <div v-if="defaultWorkspaceMode === 'custom'"
                   class="rounded-xl border border-gray-200 dark:border-gray-600 bg-gray-50/80 dark:bg-gray-750/40 p-3 space-y-2">
                <label class="text-xs font-medium text-gray-500 dark:text-gray-500">
                  {{ t('settings.workspaceCustom') || 'Custom Path' }}
                </label>
                <div class="flex items-center gap-2">
                  <input type="text" v-model="defaultWorkspacePath" @blur="handleWorkspaceSettingChange"
                         :placeholder="t('common.absolutePath') || '/path/to/workspace'"
                         class="flex-1 min-w-0 px-3 py-2 text-sm bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50">
                  <button type="button" @click="openWorkspacePathPicker" :disabled="workspaceSettingSaving"
                          class="shrink-0 px-3 py-2 text-sm font-medium bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors disabled:opacity-50 flex items-center gap-1.5">
                    <i data-lucide="folder-open" class="w-3.5 h-3.5"></i>
                    {{ t('common.browse') || 'Browse' }}
                  </button>
                </div>
              </div>
            </div>
          </card-section>

          <!-- New-session approval mode -->
          <card-section icon="shield" :title="t('settings.defaultApprovalMode') || 'New Session Approval Mode'" body-class="p-4 space-y-3">
            <template #title>
              {{ t('settings.defaultApprovalMode') || 'New Session Approval Mode' }}
              <p class="text-xs text-gray-500 dark:text-gray-500 mt-1 ml-6 font-normal">
                {{ t('settings.defaultApprovalModeDesc') || 'Deny/Allow are frozen into new sessions (winning over auto-approve; forks included); Ask follows the global default approval mode. Existing sessions are unaffected; the toolbar pill still overrides per session.' }}
              </p>
            </template>
            <template #header-actions>
              <span v-if="approvalModeSaving"
                    class="text-xs text-gray-500 dark:text-gray-500 whitespace-nowrap">
                {{ t('common.loading') || 'Saving...' }}
              </span>
            </template>
            <radio-group :model-value="defaultApprovalMode" layout="card"
                         :options="defaultApprovalModeOptions"
                         @update:model-value="setDefaultApprovalMode($event)"></radio-group>
          </card-section>
        </div>

        <!-- Advanced Tab -->
        <div v-if="activeTab === 'advanced'" class="space-y-4">
          <card-section icon="pen-tool" :title="t('settings.advanced') || 'Advanced'">
            <div class="space-y-4">
              <div class="text-center py-8 text-gray-500 dark:text-gray-500">
                <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round" class="mx-auto mb-3 opacity-50"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                <p class="text-sm">{{ t('settings.advancedDesc') || 'Advanced settings will be available soon' }}</p>
              </div>
            </div>
          </card-section>
        </div>

        <!-- Security Tab -->
        <div v-if="activeTab === 'security'" class="space-y-4">
          <!-- WebUI access gate: key + device sessions. Transport-level only —
               it never touches runs, tools or providers. -->
          <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
            <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
              <h3 class="font-medium text-gray-900 dark:text-white flex items-center gap-2">
                <i data-lucide="key-round" class="w-4 h-4 text-primary"></i>
                {{ t('settings.access') || 'WebUI Access' }}
              </h3>
              <button @click="loadAccess"
                      class="text-xs text-primary hover:text-primary-dark transition-colors flex items-center gap-1">
                <i data-lucide="refresh-cw" class="w-3 h-3"></i>
                {{ t('common.refresh') || 'Refresh' }}
              </button>
            </div>
            <div class="p-4 space-y-4">
              <p class="text-xs text-gray-500 dark:text-gray-500">
                {{ t('settings.accessDesc') || 'A new device must enter the access key. The server stores only its hash, so it can never be shown again.' }}
              </p>

              <!-- Session lifetime -->
              <div class="flex items-center justify-between gap-3">
                <div class="min-w-0">
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.sessionTtl') || 'Session lifetime' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.sessionTtlDesc') || 'How long a device stays signed in after the key is entered' }}</p>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <select v-model="accessConfig.ttl"
                          class="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                    <option v-for="opt in accessTtlOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                  </select>
                  <button @click="saveAccessTtl" :disabled="accessSaving"
                          class="px-3 py-1.5 text-sm font-medium bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors disabled:opacity-50">
                    {{ t('common.save') || 'Save' }}
                  </button>
                </div>
              </div>

              <!-- Sliding expiry -->
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.slidingTtl') || 'Sliding expiry' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.slidingTtlDesc') || 'Push the expiry forward on activity (off = absolute deadline)' }}</p>
                </div>
                <toggle-switch v-model="accessConfig.sliding" @update:model-value="saveAccessSliding"></toggle-switch>
              </div>

              <!-- Devices -->
              <div class="pt-3 border-t border-gray-100 dark:border-gray-700">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-xs font-medium text-gray-600 dark:text-gray-300">{{ t('settings.devices') || 'Signed-in devices' }}</span>
                  <button @click="logoutDevice"
                          class="text-xs text-red-500 hover:text-red-600 transition-colors">
                    {{ t('settings.logoutDevice') || 'Sign out this device' }}
                  </button>
                </div>
                <div v-if="accessDevices.length === 0" class="text-xs text-gray-500 dark:text-gray-500 py-2">
                  {{ t('settings.noDevices') || 'No active device sessions' }}
                </div>
                <div v-else class="space-y-1 max-h-56 overflow-y-auto pr-0.5">
                  <div v-for="device in accessDevices" :key="device.id"
                       class="flex items-center gap-2 p-2 rounded-lg border border-gray-100 dark:border-gray-700 text-xs">
                    <div class="min-w-0 flex-1">
                      <div class="truncate text-gray-700 dark:text-gray-200">
                        {{ deviceLabel(device) }}
                        <span v-if="device.current"
                              class="ml-1 px-1 py-0.5 rounded bg-primary/10 text-primary">
                          {{ t('settings.thisDevice') || 'This device' }}
                        </span>
                      </div>
                      <div class="text-gray-400 dark:text-gray-500 truncate">
                        {{ device.ip || '—' }} · {{ t('settings.expiresAt') || 'expires' }} {{ formatStamp(device.expires_at) }}
                      </div>
                    </div>
                    <button @click="revokeDevice(device)" :disabled="device.current"
                            :title="t('common.delete') || 'Revoke'"
                            class="shrink-0 p-1 rounded text-gray-400 hover:text-red-500 transition-colors disabled:opacity-30 disabled:cursor-not-allowed">
                      <i data-lucide="shield-x" class="w-4 h-4"></i>
                    </button>
                  </div>
                </div>
              </div>

              <!-- Rotate -->
              <div class="pt-3 border-t border-gray-100 dark:border-gray-700 space-y-2">
                <div class="flex items-center justify-between gap-3">
                  <div>
                    <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.rotateKey') || 'Rotate access key' }}</label>
                    <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.rotateKeyDesc') || 'Shows a new key first; nothing changes until you apply it' }}</p>
                  </div>
                  <button @click="rotateAccessKey" :disabled="accessRotating"
                          class="shrink-0 px-3 py-1.5 text-sm font-medium bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors disabled:opacity-50">
                    {{ t('settings.rotateKeyAction') || 'Rotate' }}
                  </button>
                </div>
                <!-- Prepared key: NOT applied yet. The current key still works,
                     so refreshing or leaving this page cannot lock anyone out. -->
                <div v-if="accessNewKey"
                     class="rounded-lg border-2 border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 p-3 space-y-2">
                  <p class="text-xs font-medium text-amber-900 dark:text-amber-100">
                    {{ t('settings.newKeyPendingTitle') || 'New key ready — not applied yet' }}
                  </p>
                  <p class="text-xs text-amber-800 dark:text-amber-200">
                    {{ t('settings.newKeyPendingDesc') || 'Save it in a password manager first. Your current key keeps working until you apply this one. Leaving this page discards it.' }}
                  </p>
                  <div class="flex items-start gap-2">
                    <code class="flex-1 block text-xs font-mono break-all text-amber-900 dark:text-amber-100 select-all bg-white/60 dark:bg-black/20 rounded p-2">{{ accessNewKey }}</code>
                    <button @click="copyNewKey"
                            class="shrink-0 px-2 py-1.5 text-xs font-medium rounded-lg border border-amber-300 dark:border-amber-700 text-amber-900 dark:text-amber-100 hover:bg-amber-100 dark:hover:bg-amber-900/40 transition-colors flex items-center gap-1">
                      <i :data-lucide="accessKeyCopied ? 'check' : 'copy'" class="w-3 h-3"></i>
                      {{ accessKeyCopied ? (t('settings.keyCopied') || 'Copied') : (t('settings.copyKey') || 'Copy') }}
                    </button>
                  </div>
                  <div class="flex items-center gap-2 pt-1">
                    <button @click="applyRotatedKey" :disabled="accessRotating"
                            class="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors disabled:opacity-50">
                      {{ t('settings.applyKeyAction') || 'I saved it — apply and sign out all devices' }}
                    </button>
                    <button @click="discardRotatedKey" :disabled="accessRotating"
                            class="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors disabled:opacity-50">
                      {{ t('common.cancel') || 'Cancel' }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
            <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
              <h3 class="font-medium text-gray-900 dark:text-white">{{ t('settings.securityConfig') || 'Security Configuration' }}</h3>
            </div>
            <div class="p-4 space-y-4">
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.approvalMode') || 'Approval Mode' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.approvalModeDesc') || 'How tool calls require user approval' }}</p>
                </div>
                <select v-model="securityConfig.approval_mode"
                        class="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                  <option value="on">{{ t('common.on') || 'On' }}</option>
                  <option value="off">{{ t('common.off') || 'Off' }}</option>
                </select>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.autoApprove') || 'Auto Approve' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.autoApproveDesc') || 'Skip tool confirmation' }}</p>
                </div>
                <toggle-switch v-model="securityConfig.auto_approve"></toggle-switch>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.allowExternalWrite') || 'Allow External Write' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.allowExternalWriteDesc') || 'Allow writes outside project workspace' }}</p>
                </div>
                <toggle-switch v-model="securityConfig.allow_external_write"></toggle-switch>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.allowExternalRead') || 'Allow External Read' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.allowExternalReadDesc') || 'Allow reading files outside the workspace (credential dirs stay forbidden)' }}</p>
                </div>
                <toggle-switch v-model="securityConfig.allow_external_read"></toggle-switch>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.networkApproval') || 'Network Command Approval' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.networkApprovalDesc') || 'Shell commands reaching external hosts require approval (loopback always free)' }}</p>
                </div>
                <toggle-switch v-model="securityConfig.network_approval"></toggle-switch>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.blockExternalPaths') || 'Block External Absolute Paths' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.blockExternalPathsDesc') || 'Hard-deny shell commands referencing paths outside the project (off = approval prompt)' }}</p>
                </div>
                <toggle-switch v-model="securityConfig.block_external_absolute_paths"></toggle-switch>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.unattendedPolicy') || 'Unattended Policy' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.unattendedPolicyDesc') || 'What happens when nobody can answer an approval prompt (headless runs)' }}</p>
                </div>
                <select v-model="securityConfig.unattended_policy"
                        class="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                  <option value="ask">{{ t('settings.unattendedAsk') || 'Wait for timeout' }}</option>
                  <option value="deny">{{ t('settings.unattendedDeny') || 'Deny immediately' }}</option>
                  <option value="approve">{{ t('settings.unattendedApprove') || 'Approve immediately' }}</option>
                </select>
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.pendingTimeout') || 'Approval Wait Timeout' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.pendingTimeoutDesc') || 'How long a run may wait for an approval decision (seconds)' }}</p>
                </div>
                <input type="number" v-model.number="securityConfig.pending_timeout" min="1" max="86400"
                       class="w-24 px-2 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 text-center">
              </div>
              <div class="flex items-center justify-between">
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.maxToolCalls') || 'Max Tool Calls' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.maxToolCallsDesc') || 'Maximum tool calls per run' }}</p>
                </div>
                <input type="number" v-model.number="securityConfig.max_tool_calls" min="0" max="100000"
                       class="w-20 px-2 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 text-center">
              </div>
              <!-- v1.16: policy knobs that used to be YAML-only. Every one
                   of them is applied live by the backend (no restart). -->
              <div class="pt-3 border-t border-gray-100 dark:border-gray-700 space-y-4">
                <div class="flex items-center justify-between">
                  <div class="min-w-0">
                    <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.trustLoopback') || 'Trust loopback' }}</label>
                    <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.trustLoopbackDesc') || 'Requests from 127.0.0.1 need no key. Turn OFF behind a reverse proxy (it forwards from loopback, which would bypass the gate)' }}</p>
                  </div>
                  <toggle-switch v-model="securityConfig.trust_loopback"></toggle-switch>
                </div>
                <div class="flex items-center justify-between gap-3">
                  <div class="min-w-0">
                    <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.sessionTtlMax') || 'Max session lifetime' }}</label>
                    <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.sessionTtlMaxDesc') || 'Upper bound (seconds) a device login may request' }}</p>
                  </div>
                  <input type="number" v-model.number="securityConfig.session_ttl_max_seconds" min="60" max="2592000"
                         class="w-28 px-2 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 text-center">
                </div>
                <div class="flex items-center justify-between">
                  <div class="min-w-0">
                    <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.auditEnabled') || 'Security audit log' }}</label>
                    <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.auditEnabledDesc') || 'Record approvals, denials and path blocks to disk' }}</p>
                  </div>
                  <toggle-switch v-model="securityConfig.audit_enabled"></toggle-switch>
                </div>
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.auditEvents') || 'Audited event types' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.auditEventsDesc') || 'One per line. Allowed: tool_execute, tool_deny, path_block, approval, pip_install' }}</p>
                  <textarea v-model="securityLists.audit_events" rows="3"
                            class="mt-1 w-full px-2 py-1.5 text-xs font-mono bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300"></textarea>
                </div>
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.auditLogPath') || 'Audit log file' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.auditLogPathDesc') || 'Relative paths resolve under the data directory' }}</p>
                  <input type="text" v-model="securityConfig.audit_log_path"
                         class="mt-1 w-full px-2 py-1.5 text-xs font-mono bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                </div>
                <div class="flex items-center justify-between gap-3">
                  <div class="min-w-0">
                    <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.errorStreakPolicy') || 'Repeated tool failures' }}</label>
                    <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.errorStreakPolicyDesc') || 'warn = keep going with a hint; block = deny after 5 consecutive failures' }}</p>
                  </div>
                  <select v-model="securityConfig.error_streak_policy"
                          class="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                    <option value="warn">warn</option>
                    <option value="block">block</option>
                  </select>
                </div>
                <div class="flex items-center justify-between gap-3">
                  <div class="min-w-0">
                    <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.shellBackend') || 'Shell backend' }}</label>
                    <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.shellBackendDesc') || 'local = the host (text checks only); docker = contained, commands run in a container' }}</p>
                  </div>
                  <select v-model="securityConfig.shell_backend"
                          class="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                    <option value="local">local</option>
                    <option value="docker">docker</option>
                  </select>
                </div>
                <template v-if="securityConfig.shell_backend === 'docker'">
                  <p class="text-xs text-amber-600 dark:text-amber-400">
                    {{ t('settings.dockerMountsWarn') || 'The container does not mount the workspace by default — configure security.shell_safety.docker_mounts in config.yaml, or commands inside it see none of the session files' }}
                  </p>
                  <div>
                    <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.dockerImage') || 'Docker image' }}</label>
                    <input type="text" v-model="securityConfig.docker_image"
                           class="mt-1 w-full px-2 py-1.5 text-xs font-mono bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300">
                  </div>
                  <div class="flex items-center justify-between">
                    <div class="min-w-0">
                      <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.dockerNetwork') || 'Container network' }}</label>
                      <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.dockerNetworkDesc') || 'Off = --network none (a networked container can exfiltrate what it reads)' }}</p>
                    </div>
                    <toggle-switch v-model="securityConfig.docker_network"></toggle-switch>
                  </div>
                </template>
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.absoluteWriteFloor') || 'Absolute write floor' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.absoluteWriteFloorDesc') || 'One glob per line. Denied for writes in EVERY mode. Must keep .boarai/config/' }}</p>
                  <textarea v-model="securityLists.absolute_write_paths" rows="5"
                            class="mt-1 w-full px-2 py-1.5 text-xs font-mono bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300"></textarea>
                </div>
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.forbiddenReadPaths') || 'Confirm-tier read paths' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.forbiddenReadPathsDesc') || 'Credential locations that prompt before being read. One glob per line' }}</p>
                  <textarea v-model="securityLists.forbidden_read_paths" rows="5"
                            class="mt-1 w-full px-2 py-1.5 text-xs font-mono bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300"></textarea>
                </div>
                <div>
                  <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('settings.forbiddenWritePaths') || 'Confirm-tier write paths' }}</label>
                  <p class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{{ t('settings.forbiddenWritePathsDesc') || 'Sensitive write targets that prompt first. One glob per line' }}</p>
                  <textarea v-model="securityLists.forbidden_write_paths" rows="4"
                            class="mt-1 w-full px-2 py-1.5 text-xs font-mono bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300"></textarea>
                </div>
              </div>
              <div class="pt-3 border-t border-gray-100 dark:border-gray-700">
                <button @click="saveSecurityConfig"
                        class="px-4 py-2 text-sm font-medium bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors flex items-center gap-2"
                        :disabled="securitySaving">
                  <i v-if="securitySaving" data-lucide="loader" class="w-4 h-4 animate-spin"></i>
                  <i v-else data-lucide="save" class="w-4 h-4"></i>
                  {{ t('common.save') || 'Save' }}
                </button>
              </div>
            </div>
          </div>
          <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
            <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
              <h3 class="font-medium text-gray-900 dark:text-white">{{ t('settings.auditLog') || 'Security Audit Log' }}</h3>
              <button @click="loadAuditLog" class="text-xs text-primary hover:text-primary-dark transition-colors flex items-center gap-1">
                <i data-lucide="refresh-cw" class="w-3 h-3"></i>
                {{ t('common.refresh') || 'Refresh' }}
              </button>
            </div>
            <div class="p-4">
              <div v-if="auditLoading" class="text-center py-4 text-sm text-gray-500">{{ t('common.loading') || 'Loading...' }}</div>
              <div v-else-if="auditRecords.length === 0" class="text-center py-4 text-sm text-gray-500">{{ t('settings.noAuditRecords') || 'No security events recorded' }}</div>
              <div v-else class="space-y-1 max-h-64 overflow-y-auto">
                <div v-for="rec in auditRecords" :key="rec._domKey"
                     class="flex items-start gap-2 p-1.5 text-xs font-mono rounded hover:bg-gray-50 dark:hover:bg-gray-700/50"
                     :class="{
                       'text-red-600 dark:text-red-400': rec.event === 'tool_deny' || rec.event === 'path_block',
                       'text-green-600 dark:text-green-400': rec.event === 'approval',
                       'text-gray-600 dark:text-gray-500': rec.event === 'tool_execute',
                     }">
                  <span class="text-gray-500 shrink-0 w-20">{{ formatTime(rec.ts) }}</span>
                  <span class="shrink-0 w-16 font-semibold">{{ rec.event }}</span>
                  <span class="truncate">{{ rec.tool || rec.reason || '' }} {{ rec.agent ? '(' + rec.agent + ')' : '' }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- About Tab -->
        <div v-if="activeTab === 'about'" class="space-y-4">
          <card-section icon="info" :title="t('settings.about')" body-class="p-0">
            <div class="p-4">
              <div class="text-center py-6">
                <svg class="w-20 h-20 mx-auto mb-4 text-primary" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <rect width="100" height="100" rx="22" fill="currentColor"/>
                  <path d="M32 46c0-10 8-18 18-18s18 8 18 18v22l-6-4-6 4-6-4-6 4-6-4-6 4z"
                        fill="none" stroke="white" stroke-width="5" stroke-linejoin="round"/>
                  <circle cx="43" cy="47" r="3" fill="white"/>
                  <circle cx="57" cy="47" r="3" fill="white"/>
                  <circle cx="43" cy="47" r="1.5" fill="currentColor"/>
                  <circle cx="57" cy="47" r="1.5" fill="currentColor"/>
                  <ellipse cx="36" cy="43" rx="4" ry="2" fill="white" transform="rotate(-25 36 43)"/>
                  <ellipse cx="64" cy="43" rx="4" ry="2" fill="white" transform="rotate(25 64 43)"/>
                </svg>
                <div class="text-xl font-bold text-gray-900 dark:text-white mb-1">BoarAi WebUI</div>
                <div class="text-sm text-gray-500 dark:text-gray-500 mb-4">{{ t('settings.version', { version: (updateStatus && updateStatus.current_version) || '—' }) }}</div>
                <div class="text-xs text-gray-500 dark:text-gray-500 max-w-md mx-auto">
                  {{ t('settings.aboutDesc') || 'BoarAi is an AI Agent Framework that enables you to build intelligent agents with ease.' }}
                </div>
                <!-- Project links: GitHub source repository + PyPI package page -->
                <div class="flex items-center justify-center gap-3 mt-5">
                  <a href="https://github.com/openboarai/boarai" target="_blank" rel="noopener"
                     :title="t('settings.viewOnGithub') || 'View source on GitHub'"
                     class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 hover:text-gray-900 dark:hover:text-white transition-colors">
                    <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/>
                    </svg>
                    <span>GitHub</span>
                  </a>
                  <a href="https://pypi.org/project/boarai/" target="_blank" rel="noopener"
                     :title="t('settings.viewOnPypi') || 'View package on PyPI'"
                     class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 hover:text-gray-900 dark:hover:text-white transition-colors">
                    <i data-lucide="package" class="w-4 h-4"></i>
                    <span>PyPI</span>
                  </a>
                </div>
              </div>
            </div>
          </card-section>

          <!-- Online Update (pip / git / source channels; refusal contract rendered when a channel is blocked) -->
          <card-section icon="refresh-cw" :title="t('settings.updateTitle') || 'Online Update'" body-class="p-4">
            <div v-if="updateLoading && !updateStatus" class="text-sm text-gray-500 dark:text-gray-500 py-2">
              <i data-lucide="loader-2" class="w-4 h-4 inline animate-spin"></i>
            </div>
            <div v-else-if="updateStatus" class="space-y-3">
              <!-- Version + channel row -->
              <div class="flex flex-wrap items-center gap-2 text-sm">
                <span class="text-gray-500 dark:text-gray-500">{{ t('settings.updateCurrent') || 'Current' }}</span>
                <span class="font-mono font-medium text-gray-900 dark:text-white">v{{ updateStatus.current_version }}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                      :title="t('settings.updateChannelHint') || 'pip, git and source installs all self-update through their own channel'">
                  {{ updateStatus.install_kind }}
                </span>
                <template v-if="updateStatus.check && updateStatus.check.latest">
                  <span class="text-gray-300 dark:text-gray-600">|</span>
                  <span class="text-gray-500 dark:text-gray-500">{{ t('settings.updateLatest') || 'Latest' }}</span>
                  <span class="font-mono font-medium" :class="updateStatus.check.update_available ? 'text-primary' : 'text-gray-900 dark:text-white'">
                    v{{ updateStatus.check.latest }}
                  </span>
                  <span v-if="!updateStatus.check.update_available" class="text-xs text-green-600 dark:text-green-400">
                    {{ t('settings.updateUpToDate') || 'Up to date' }}
                  </span>
                </template>
              </div>

              <!-- Check error (network etc.) -->
              <div v-if="updateStatus.check && updateStatus.check.error" class="text-xs text-amber-600 dark:text-amber-400 break-all">
                {{ updateStatus.check.error }}
              </div>

              <!-- Refusal contract for non-pip installs -->
              <div v-if="updateStatus.refusal" class="text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-800 rounded-lg p-3 space-y-1">
                <div>{{ updateStatus.refusal.message }}</div>
                <div v-if="updateStatus.refusal.update_command" class="font-mono break-all">{{ updateStatus.refusal.update_command }}</div>
              </div>

              <!-- Worker progress -->
              <div v-if="updateStatus.worker && updateStatus.worker_active" class="text-sm">
                <div class="flex items-center gap-2">
                  <i data-lucide="loader-2" class="w-4 h-4 text-primary animate-spin"></i>
                  <span class="text-primary">{{ t('settings.updatePhase_' + updateStatus.worker.phase) || updateStatus.worker.phase }}</span>
                  <span class="font-mono text-xs text-gray-500 dark:text-gray-500">
                    {{ updateStatus.worker.from_version }} → {{ updateStatus.worker.target_version }}
                  </span>
                </div>
                <div v-if="updateStatus.worker.error" class="text-xs text-red-500 mt-1 break-all">{{ updateStatus.worker.error }}</div>
              </div>
              <!-- Last finished attempt (failed) -->
              <div v-else-if="updateStatus.worker && updateStatus.worker.phase === 'failed'" class="text-xs text-red-500 break-all">
                {{ t('settings.updateLastFailed') || 'Last update failed' }}: {{ updateStatus.worker.error || '' }}
              </div>

              <!-- Actions -->
              <div class="flex flex-wrap gap-2 pt-1">
                <base-button size="sm" icon="search" variant="secondary" :loading="updateBusy === 'check'" :disabled="updateBusy !== ''" @click="checkForUpdate">
                  {{ t('settings.updateCheck') || 'Check for updates' }}
                </base-button>
                <base-button v-if="updateStatus.can_update && updateStatus.check && updateStatus.check.update_available"
                             size="sm" icon="download" variant="primary" :loading="updateBusy === 'apply'" :disabled="updateBusy !== ''"
                             @click="applyUpdate(false)">
                  {{ t('settings.updateApply', { version: updateStatus.check.latest }) || ('Update to v' + updateStatus.check.latest) }}
                </base-button>
                <base-button v-if="updateStatus.can_update && updateStatus.previous_version"
                             size="sm" icon="undo-2" variant="secondary" :loading="updateBusy === 'rollback'" :disabled="updateBusy !== ''"
                             @click="applyUpdate(true)">
                  {{ t('settings.updateRollback', { version: updateStatus.previous_version }) || ('Roll back to v' + updateStatus.previous_version) }}
                </base-button>
              </div>
            </div>
          </card-section>
        </div>

      </div>
    </div>

    <!-- Directory Picker Modal -->
    <directory-picker-modal :model-value="showDirPicker"
                            :title="t('settings.selectWorkspace') || 'Select default workspace directory'"
                            :roots="dirPicker.roots"
                            :path="dirPicker.path"
                            :path-input="dirPicker.pathInput"
                            :parent-path="dirPicker.parent"
                            :entries="dirPicker.entries"
                            :loading="dirPicker.loading"
                            :error="dirPicker.error"
                            :selected="dirPicker.selected"
                            @update:model-value="closeDirPicker"
                            @update:path-input="dirPicker.pathInput = $event"
                            @browse="browseDirForWorkspace($event)"
                            @go-up="goDirUp"
                            @select="dirPicker.selected = $event"
                            @confirm="confirmDirPicker">
    </directory-picker-modal>
  `,
  
  data() {
    return {
      activeTab: localStorage.getItem('boarai-tab-settings') || 'appearance',
      theme: localStorage.getItem('boarai-theme') || 'light',
      colorTheme: localStorage.getItem('boarai-color') || 'cyan',
      language: window.i18n.getCurrentLanguage(),
      homepage: localStorage.getItem('boarai-homepage') || 'dashboard',
      // Security config state
      compressionThreshold: 70,
      defaultWorkspaceMode: 'session_dir',
      defaultWorkspacePath: '',
      workspaceSettingSaving: false,
      // New-session tool-approval mode: 'deny' | 'ask' | 'allow' (server
      // authoritative, stored as session.default_approval_mode).
      defaultApprovalMode: 'ask',
      approvalModeSaving: false,
      // Directory picker for workspace path
      showDirPicker: false,
      dirPicker: {
        path: '',
        pathInput: '',
        parent: null,
        entries: [],
        selected: '',
        loading: false,
        error: '',
        roots: [],
      },
      securityConfig: {
        approval_mode: 'on',
        auto_approve: false,
        allow_external_write: false,
        allow_external_read: true,
        max_tool_calls: 50,
        unattended_policy: 'ask',
        pending_timeout: 600,
        network_approval: true,
        block_external_absolute_paths: false,
        // v1.16 — the knobs that used to be YAML-only.
        trust_loopback: true,
        session_ttl_max_seconds: 2592000,
        audit_enabled: true,
        audit_log_path: '',
        shell_backend: 'local',
        docker_image: 'python:3.12-slim',
        docker_network: false,
        error_streak_policy: 'warn',
      },
      // List-valued settings: edited as one entry per line, sent as arrays.
      securityLists: {
        absolute_write_paths: '',
        forbidden_read_paths: '',
        forbidden_write_paths: '',
        audit_events: '',
      },
      auditRecords: [],
      auditLoading: false,
      securitySaving: false,
      // WebUI access gate (key + device sessions). Lifetime labels are derived
      // in a computed so they follow the language switch.
      accessConfig: { ttl: '', sliding: false },
      accessTtlSeconds: [],
      accessDevices: [],
      accessSaving: false,
      accessRotating: false,
      accessNewKey: '',
      accessKeyCopied: false,
      // Online update (About tab): status payload + in-flight action marker
      updateStatus: null,
      updateLoading: false,
      updateBusy: '',
      colorThemes: [
        { code: 'cyan', name: 'Indigo', hex: '#6366f1' },
        { code: 'purple', name: 'Violet', hex: '#8b5cf6' }
      ],
      tabs: [
        { id: 'providers', label: 'Providers' },
        { id: 'appearance', label: 'Appearance' },
        { id: 'sessions', label: 'Sessions' },
        { id: 'general', label: 'General' },
        { id: 'security', label: 'Security' },
        { id: 'advanced', label: 'Advanced' },
        { id: 'about', label: 'About' }
      ],
      // Provider management — master/detail: '' = nothing open, 'edit' = an
      // existing provider is loaded into the form, 'new' = blank add form.
      providers: [],
      providerFormMode: '',
      editingProvider: null,
      savingProvider: false,
      // Column 3 shows either the provider form ('provider') or the settings of
      // the model row clicked in column 2 ('model').
      detailMode: 'provider',
      modelDetail: null,
      // Capability toggle definitions for the model settings panel (UI only;
      // server sync is a later step).
      modelCaps: [
        { key: 'image', icon: 'image', label: 'Image input' },
        { key: 'audio', icon: 'mic', label: 'Audio input' },
        { key: 'video', icon: 'video', label: 'Video input' },
        { key: 'image_gen', icon: 'image-plus', label: 'Image generation' },
        { key: 'video_gen', icon: 'clapperboard', label: 'Video generation' },
      ],
      // Whether the initial provider list load already happened (drives the
      // "auto-select the first provider" behaviour of the detail column).
      providersLoaded: false,
      // Serialized form as loaded, used to warn before discarding edits.
      formSnapshot: '',
      // Capability model cards
      capabilities: [
        { key: 'chat', icon: 'message-square', label: '对话默认模型' },
        { key: 'vision', icon: 'eye', label: '视觉分析' },
        { key: 'audio_input', icon: 'mic', label: '音频分析' },
        { key: 'image_gen', icon: 'image', label: '图片生成' },
        { key: 'audio_gen', icon: 'volume-2', label: '音频生成' },
      ],
      capStates: {},
      form: {
        name: '',
        provider_type: 'openai',
        api_key: '',
        api_base: '',
        fetched_models: [],
        selected_models: [],
        fetching_models: false,
        manual_model: '',
      },
    };
  },
  
  mounted() {
    document.addEventListener('click', this._onCapDocClick);
    // Initialize tab labels based on current language
    this.updateTabLabels();
    this._initCapStates();
    // Load providers
    this.loadProviders();
    // Load security config and audit log
    this.loadSecurityConfig();
    this.loadAuditLog();
    // Load the access gate state (key policy + signed-in devices)
    this.loadAccess();
    // Load session settings (compression threshold)
    this.loadCompressionThreshold();
    // Online update: status once + live worker events (bus → WS mirror)
    this.loadUpdateStatus();
    if (window.BoarAiStore) {
      window.BoarAiStore.onUpdateEvent = (type, payload) => this.onUpdateEvent(type, payload);
    }
  },
  
  // Icons hydrate automatically via app.js MutationObserver.
  updated() {},

  watch: {
    'form.provider_type'() {
      this.form.fetched_models = [];
    },
    'form.api_key'() {
      this.form.fetched_models = [];
    },
    'form.api_base'() {
      this.form.fetched_models = [];
    },
  },
  
  beforeUnmount() {
    document.removeEventListener('click', this._onCapDocClick);
    if (window.BoarAiStore && window.BoarAiStore.onUpdateEvent) {
      window.BoarAiStore.onUpdateEvent = null;
    }
  },
  
  computed: {
    /** Lifetime labels for the access card; re-derived on language change. */
    accessTtlOptions() {
      return this.accessTtlSeconds.map((seconds) => ({
        value: String(seconds),
        label: this.durationLabel(seconds),
      }));
    },
    workspaceModeOptions() {
      return [
        { value: 'session_dir', label: this.t('settings.workspaceSessionDir') || 'Session Directory', desc: this.t('settings.workspaceSessionDirDesc') || 'Each session gets its own workspace under the session folder', icon: 'folder-plus' },
        { value: 'shared_workspace', label: this.t('settings.workspaceShared') || 'Shared Workspace', desc: this.t('settings.workspaceSharedDesc') || 'All sessions share data_dir/workspace', icon: 'folders' },
        { value: 'custom', label: this.t('settings.workspaceCustom') || 'Custom Path', desc: this.t('settings.workspaceCustomDesc') || 'Use a specific directory path', icon: 'folder-input' },
      ];
    },
    defaultApprovalModeOptions() {
      return [
        { value: 'deny', label: this.t('sessions.approvalDeny') || 'Deny', desc: this.t('settings.approvalDenyDesc') || 'Only read-only / safe tools run; everything else is rejected without prompting', icon: 'shield-x' },
        { value: 'ask', label: this.t('sessions.approvalAsk') || 'Ask', desc: this.t('settings.approvalAskDesc') || 'Tools with side effects prompt for confirmation first', icon: 'shield-question-mark' },
        { value: 'allow', label: this.t('sessions.approvalAllow') || 'Allow', desc: this.t('settings.approvalAllowDesc') || 'No prompts — tools run directly (the absolute write floor still denies)', icon: 'shield-check' },
      ];
    },
  },

  methods: {
    _initCapStates() {
      for (const cap of this.capabilities) {
        this.capStates[cap.key] = {
          open: false,
          // Open the menu above the tile when the viewport lacks room below.
          dropUp: false,
          // Drill-down menu level: 'provider' list or that provider's 'model' list.
          level: 'provider',
          provider: '',
          model: '',
          models: [],
          saving: false,
          savedProvider: '',
          savedModel: '',
        };
      }
    },
    
    getCapState(key) {
      return this.capStates[key] || {};
    },

    /** Row has a selection that is not the persisted one → show Save. */
    capDirty(key) {
      const s = this.capStates[key];
      if (!s || !s.provider || !s.model) return false;
      return s.provider !== s.savedProvider || s.model !== s.savedModel;
    },

    /** Row is persisted (and unchanged) → show the subtle check mark. */
    capSaved(key) {
      const s = this.capStates[key];
      return !!(s && s.provider && s.model && !this.capDirty(key));
    },
    
    _onCapDocClick(e) {
      for (const cap of this.capabilities) {
        const s = this.capStates[cap.key];
        if (!s || !s.open) continue;
        const ref = this.$refs['capDropdown_' + cap.key];
        const el = Array.isArray(ref) ? ref[0] : ref;
        if (el && !el.contains(e.target)) {
          s.open = false;
        }
      }
    },
    
    capToggle(key) {
      const s = this.capStates[key];
      if (!s) return;
      // Close others
      for (const cap of this.capabilities) {
        if (cap.key !== key) this.capStates[cap.key].open = false;
      }
      s.open = !s.open;
      if (s.open) {
        // Reopening a configured tile lands straight on its model list.
        s.level = s.provider ? 'model' : 'provider';
        // The card usually sits at the page bottom: flip the menu up when
        // fewer than ~15 menu rows fit below the tile.
        const ref = this.$refs['capDropdown_' + key];
        const el = Array.isArray(ref) ? ref[0] : ref;
        const room = el ? window.innerHeight - el.getBoundingClientRect().bottom : 9999;
        s.dropUp = room < 260;
      }
    },

    capBack(key) {
      const s = this.capStates[key];
      if (!s) return;
      s.level = 'provider';
    },
    
    capSelectProvider(key, provider) {
      const s = this.capStates[key];
      if (!s) return;
      s.provider = provider.name;
      s.model = '';
      s.models = provider.supported_models || [];
      s.level = 'model';
    },
    
    capSelectModel(key, model) {
      const s = this.capStates[key];
      if (!s) return;
      s.model = model;
      s.open = false;
    },
    
    async saveCapabilityModel(key) {
      const s = this.capStates[key];
      if (!s || !s.provider || !s.model) return;
      s.saving = true;
      try {
        if (key === 'chat') {
          await window.BoarAiStore.api.setDefaultModel(s.provider, s.model);
        } else {
          await window.BoarAiStore.api.setCapabilityModel(key, s.provider, s.model);
        }
        s.savedProvider = s.provider;
        s.savedModel = s.model;
        window.BoarAiStore.actions.success(this.t('notifications.defaultModelSet'));
      } catch (error) {
        console.error(`Failed to set ${key} model:`, error);
        window.BoarAiStore.actions.error(error.message || this.t('notifications.setDefaultModelFailed'));
      } finally {
        s.saving = false;
      }
    },
    
    switchSettingsTab(tabId) {
      this.activeTab = tabId;
      localStorage.setItem('boarai-tab-settings', tabId);
    },

    // Override global I18nMixin.handleLanguageChange (called via the mixin's
    // language-changed listener): keep the reactive currentLanguage + the
    // dropdown `language` field + refresh tab labels.
    handleLanguageChange() {
      this.currentLanguage = window.i18n ? window.i18n.getCurrentLanguage() : 'en';
      this.language = this.currentLanguage;
      this.updateTabLabels();
    },
    
    updateTabLabels() {
      // Note: no `icon` fields — TabBar renders icons via data-lucide (lucide
      // names). These tabs intentionally stay text-only.
      this.tabs = [
        { id: 'providers', label: this.t('settings.providers') || 'Providers' },
        { id: 'appearance', label: this.t('settings.appearance') || 'Appearance' },
        { id: 'sessions', label: this.t('settings.sessions') || 'Sessions' },
        { id: 'general', label: this.t('settings.general') || 'General' },
        { id: 'security', label: this.t('settings.security') || 'Security' },
        { id: 'advanced', label: this.t('settings.advanced') || 'Advanced' },
        { id: 'about', label: this.t('settings.about') || 'About' }
      ];
    },
    
    handleThemeChange() {
      // BoarTheme is the single source of truth (DOM + storage + event)
      if (window.BoarTheme) {
        window.BoarTheme.set(this.theme);
      } else {
        localStorage.setItem('boarai-theme', this.theme);
        document.documentElement.classList.toggle('dark', this.theme === 'dark');
        document.documentElement.setAttribute('data-theme', this.theme);
        window.dispatchEvent(new CustomEvent('theme-changed', { detail: { theme: this.theme } }));
      }
      // Keep root app state in sync when available
      try {
        const exposed = window.BoarAiApp?.app?._instance?.exposed;
        if (exposed?.setTheme) exposed.setTheme(this.theme);
      } catch (_) {}
    },
    
    setColorTheme(colorCode) {
      this.colorTheme = colorCode;
      if (window.BoarTheme) {
        window.BoarTheme.setColor(colorCode);
      } else {
        localStorage.setItem('boarai-color', colorCode);
        document.documentElement.setAttribute('data-color', colorCode);
        window.dispatchEvent(new CustomEvent('color-changed', { detail: { color: colorCode } }));
      }
    },

    // ── Session Settings ──

    async loadCompressionThreshold() {
      try {
        const res = await fetch('/api/settings/session');
        const result = await res.json();
        if (result.success && result.data) {
          const t = result.data.compression_threshold;
          if (typeof t === 'number') {
            this.compressionThreshold = Math.round(t * 100);
          }
          // Load workspace settings
          if (result.data.default_workspace_mode) {
            this.defaultWorkspaceMode = result.data.default_workspace_mode;
          }
          if (result.data.default_workspace_path !== undefined) {
            this.defaultWorkspacePath = result.data.default_workspace_path;
          }
          // New-session approval mode
          if (result.data.default_approval_mode) {
            this.defaultApprovalMode = result.data.default_approval_mode;
          }
        }
      } catch (e) {
        console.warn('Failed to load compression threshold:', e);
      }
    },

    async handleCompressionThresholdChange() {
      const ratio = this.compressionThreshold / 100;
      try {
        const res = await fetch('/api/settings/session', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ compression_threshold: ratio }),
        });
        const result = await res.json();
        if (result.success) {
          if (result.data && typeof result.data.compression_threshold === 'number') {
            this.compressionThreshold = Math.round(result.data.compression_threshold * 100);
          }
        }
      } catch (e) {
        console.warn('Failed to save compression threshold:', e);
      }
    },

    setWorkspaceMode(mode) {
      this.defaultWorkspaceMode = mode;
      this.handleWorkspaceSettingChange();
    },

    async handleWorkspaceSettingChange() {
      this.workspaceSettingSaving = true;
      try {
        const res = await fetch('/api/settings/session', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            default_workspace_mode: this.defaultWorkspaceMode,
            default_workspace_path: this.defaultWorkspacePath
          }),
        });
        const result = await res.json();
        if (result.success && result.data) {
          if (result.data.default_workspace_mode) {
            this.defaultWorkspaceMode = result.data.default_workspace_mode;
          }
          if (result.data.default_workspace_path !== undefined) {
            this.defaultWorkspacePath = result.data.default_workspace_path;
          }
        }
      } catch (e) {
        console.warn('Failed to save workspace settings:', e);
      } finally {
        this.workspaceSettingSaving = false;
      }
    },

    async setDefaultApprovalMode(mode) {
      const prev = this.defaultApprovalMode;
      this.defaultApprovalMode = mode;
      this.approvalModeSaving = true;
      try {
        const res = await fetch('/api/settings/session', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ default_approval_mode: mode }),
        });
        const result = await res.json();
        if (!result.success) {
          throw new Error(result.error?.message || 'Failed to save approval mode');
        }
        if (result.data && result.data.default_approval_mode) {
          this.defaultApprovalMode = result.data.default_approval_mode;
        }
      } catch (e) {
        // Never leave the card showing a mode the server did not accept.
        this.defaultApprovalMode = prev;
        console.warn('Failed to save default approval mode:', e);
      } finally {
        this.approvalModeSaving = false;
      }
    },

    async openWorkspacePathPicker() {
      this.showDirPicker = true;
      this.dirPicker.error = '';
      this.dirPicker.selected = this.defaultWorkspacePath || '';
      try {
        const rootsData = await window.BoarAiStore.api.fsRoots();
        this.dirPicker.roots = rootsData.roots || [];
      } catch (e) {
        this.dirPicker.roots = [];
      }
      const start = this.defaultWorkspacePath || '';
      await this.browseDirForWorkspace(start || null);
    },

    closeDirPicker() {
      this.showDirPicker = false;
    },

    async browseDirForWorkspace(path) {
      this.dirPicker.loading = true;
      this.dirPicker.error = '';
      try {
        const data = await window.BoarAiStore.api.fsBrowse(path || '', false);
        this.dirPicker.path = data.path || '';
        this.dirPicker.pathInput = data.path || '';
        this.dirPicker.parent = data.parent || null;
        this.dirPicker.entries = data.entries || [];
        this.dirPicker.selected = data.path || '';
      } catch (e) {
        this.dirPicker.error = String(e?.message || e || 'Browse failed');
        this.dirPicker.entries = [];
      } finally {
        this.dirPicker.loading = false;
      }
    },

    goDirUp() {
      if (this.dirPicker.parent) this.browseDirForWorkspace(this.dirPicker.parent);
    },

    async confirmDirPicker() {
      const path = this.dirPicker.selected || this.dirPicker.path;
      if (!path) {
        window.BoarAiStore.actions.error('Please select a folder');
        return;
      }
      this.defaultWorkspacePath = path;
      this.showDirPicker = false;
      await this.handleWorkspaceSettingChange();
    },

    // ── Security ──

    // ── Online Update (About tab) ──────────────────────────────────────

    async loadUpdateStatus() {
      this.updateLoading = true;
      try {
        const res = await fetch('/api/update/status');
        if (res.ok) this.updateStatus = await res.json();
      } catch (e) {
        console.warn('Failed to load update status:', e);
      } finally {
        this.updateLoading = false;
      }
    },

    async checkForUpdate() {
      this.updateBusy = 'check';
      try {
        const res = await fetch('/api/update/check', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force: true }),
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || ('HTTP ' + res.status));
        await this.loadUpdateStatus();
        const store = window.BoarAiStore;
        if (result.update_available && store) {
          store.actions.info(this.t('settings.updateFound', { version: result.latest }) || ('New version available: v' + result.latest), 6000);
        } else if (store) {
          store.actions.success(this.t('settings.updateUpToDate') || 'Up to date', 3000);
        }
      } catch (e) {
        if (window.BoarAiStore) window.BoarAiStore.actions.error(String(e.message || e), 6000);
      } finally {
        this.updateBusy = '';
      }
    },

    async applyUpdate(rollback) {
      this.updateBusy = rollback ? 'rollback' : 'apply';
      try {
        const res = await fetch('/api/update/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(rollback ? { rollback: true } : {}),
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || ('HTTP ' + res.status));
        await this.loadUpdateStatus();
        if (window.BoarAiStore) {
          window.BoarAiStore.actions.info(this.t('settings.updateStarted') || 'Update started — the server restarts when the worker finishes', 8000);
        }
        // The worker STOPS this server before installing: the WS mirror dies
        // with it, so the authoritative progress channel from here on is
        // REST polling of the worker's status file via /api/update/status.
        this._pollUpdateLifecycle(result.target_version || '');
      } catch (e) {
        if (window.BoarAiStore) window.BoarAiStore.actions.error(String(e.message || e), 8000);
        await this.loadUpdateStatus();
      } finally {
        this.updateBusy = '';
      }
    },

    onUpdateEvent(type, payload) {
      // Best-effort live mirror (update.* bus events → WS). Only reachable
      // until the worker stops this server; REST polling below is the
      // authoritative channel across the restart.
      if (!this.updateStatus) return;
      if (type === 'update.progress') {
        this.updateStatus.worker = payload;
        this.updateStatus.worker_active = true;
      } else if (type === 'update.failed') {
        this.updateStatus.worker = payload;
        this.updateStatus.worker_active = false;
        if (window.BoarAiStore) {
          window.BoarAiStore.actions.error((this.t('settings.updateFailed') || 'Update failed') + ': ' + (payload.error || ''), 10000);
        }
        this.loadUpdateStatus();
      } else if (type === 'update.done') {
        this.updateStatus.worker = payload;
        this.updateStatus.worker_active = false;
        window.location.reload();
      } else if (type === 'update.available') {
        this.loadUpdateStatus();
      }
    },

    async _pollUpdateLifecycle(targetVersion) {
      // Poll /api/update/status across stop → install → restart. The
      // endpoint is unreachable while the server is down/restarting — that
      // is EXPECTED and simply keeps the last known phase on screen. On
      // terminal worker state: reload (fresh static assets + new version)
      // or surface the failure. Hard stop after ~6 minutes.
      const deadline = Date.now() + 360000;
      while (Date.now() < deadline) {
        await new Promise(r => setTimeout(r, 2000));
        let status = null;
        try {
          const res = await fetch('/api/update/status');
          if (res.ok) status = await res.json();
        } catch (e) { /* server mid-restart — keep polling */ }
        if (!status) continue;
        this.updateStatus = status;
        const worker = status.worker;
        if (!worker) continue;
        if (worker.phase === 'done' && (!targetVersion || status.current_version === targetVersion)) {
          if (window.BoarAiStore) {
            window.BoarAiStore.actions.success(this.t('settings.updatePhase_done') || 'Update complete', 5000);
          }
          window.location.reload();
          return;
        }
        if (worker.phase === 'failed') {
          if (window.BoarAiStore) {
            window.BoarAiStore.actions.error((this.t('settings.updateFailed') || 'Update failed') + ': ' + (worker.error || ''), 10000);
          }
          return;
        }
      }
    },

    async loadSecurityConfig() {
      try {
        const res = await fetch('/api/security/config');
        const result = await res.json();
        if (result.success && result.data) {
          const data = result.data;
          Object.assign(this.securityConfig, data);
          // List-valued settings are edited as text (one entry per line);
          // split back into arrays on save.
          const asText = (value) => (Array.isArray(value) ? value.join('\n') : '');
          this.securityLists.absolute_write_paths = asText(data.absolute_write_paths);
          this.securityLists.forbidden_read_paths = asText(data.forbidden_read_paths);
          this.securityLists.forbidden_write_paths = asText(data.forbidden_write_paths);
          this.securityLists.audit_events = asText(data.audit_events);
        }
      } catch (e) {
        console.warn('Failed to load security config:', e);
      }
    },

    /** One textarea/input value → list of trimmed entries (newlines or commas). */
    _splitSecurityList(text) {
      return String(text || '')
        .split(/[\n,]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    },

    async loadAuditLog() {
      this.auditLoading = true;
      try {
        const res = await fetch('/api/security/audit?limit=100');
        const result = await res.json();
        this.auditRecords = (result.data?.records || []).slice(0, 100).map((record, index) => ({
          ...record,
          _domKey: record.id || record.audit_id || [
            record.ts || record.timestamp || '',
            record.event || '',
            record.session_id || '',
            record.tool || record.tool_name || '',
            index,
          ].join(':'),
        }));
      } catch (e) {
        console.warn('Failed to load audit log:', e);
        this.auditRecords = [];
      } finally {
        this.auditLoading = false;
      }
    },

    async saveSecurityConfig() {
      this.securitySaving = true;
      try {
        // List-valued fields: an EMPTY textarea is sent as "unchanged", not
        // as an empty array — the server rejects empty lists (an audit trail
        // with no event types, or no write floor, is never a legitimate
        // state), and a failed GET must not turn Save into a wall of 400s.
        const body = {
          approval_mode: this.securityConfig.approval_mode,
          auto_approve: this.securityConfig.auto_approve,
          max_tool_calls: this.securityConfig.max_tool_calls,
          allow_external_write: this.securityConfig.allow_external_write,
          allow_external_read: this.securityConfig.allow_external_read,
          unattended_policy: this.securityConfig.unattended_policy,
          pending_timeout: this.securityConfig.pending_timeout,
          network_approval: this.securityConfig.network_approval,
          block_external_absolute_paths: this.securityConfig.block_external_absolute_paths,
          trust_loopback: this.securityConfig.trust_loopback,
          session_ttl_max_seconds: this.securityConfig.session_ttl_max_seconds,
          audit_enabled: this.securityConfig.audit_enabled,
          shell_backend: this.securityConfig.shell_backend,
          docker_image: this.securityConfig.docker_image,
          docker_network: this.securityConfig.docker_network,
          error_streak_policy: this.securityConfig.error_streak_policy,
        };
        const listFields = {
          audit_events: this.securityLists.audit_events,
          absolute_write_paths: this.securityLists.absolute_write_paths,
          forbidden_read_paths: this.securityLists.forbidden_read_paths,
          forbidden_write_paths: this.securityLists.forbidden_write_paths,
        };
        for (const [field, text] of Object.entries(listFields)) {
          const items = this._splitSecurityList(text);
          if (items.length) body[field] = items;
        }
        if (this.securityConfig.audit_log_path) {
          body.audit_log_path = this.securityConfig.audit_log_path;
        }
        const res = await fetch('/api/security/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const result = await res.json();
        if (result.success) {
          window.BoarAiStore?.actions?.success(this.t('notifications.securityConfigSaved'));
        } else {
          window.BoarAiStore?.actions?.error(result.error?.message || this.t('notifications.failedToSave'));
        }
      } catch (e) {
        console.error('Save security config failed:', e);
        window.BoarAiStore?.actions?.error(this.t('notifications.failedToSaveSecurityConfig'));
      } finally {
        this.securitySaving = false;
      }
    },

    // ==================== WebUI access gate ====================

    /** Human label for one allowed session lifetime (seconds). */
    durationLabel(seconds) {
      const value = Number(seconds) || 0;
      if (value < 3600) return (Math.round(value / 60) || 1) + (this.t('settings.duration.minutes') || ' min');
      if (value < 86400) return Math.round(value / 3600) + (this.t('settings.duration.hours') || ' hours');
      const days = Math.round(value / 86400);
      return days + (this.t('settings.duration.days') || ' days');
    },

    /** Epoch seconds → local `MM-DD HH:MM` (device list). */
    formatStamp(seconds) {
      const value = Number(seconds);
      if (!value) return '—';
      const date = new Date(value * 1000);
      const pad = (n) => String(n).padStart(2, '0');
      return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    },

    /** Condense a User-Agent into "Chrome · Windows"; unknown stays short. */
    deviceLabel(device) {
      const ua = (device && device.user_agent) || '';
      if (!ua) return this.t('settings.unknownDevice') || 'Unknown device';
      let browser = '';
      if (/Edg\//.test(ua)) browser = 'Edge';
      else if (/OPR\//.test(ua)) browser = 'Opera';
      else if (/Firefox\//.test(ua)) browser = 'Firefox';
      else if (/Chrome\//.test(ua)) browser = 'Chrome';
      else if (/Safari\//.test(ua)) browser = 'Safari';
      else if (/curl\//i.test(ua)) browser = 'curl';
      let platform = '';
      if (/Windows/.test(ua)) platform = 'Windows';
      else if (/Android/.test(ua)) platform = 'Android';
      else if (/iPhone|iPad|iOS/.test(ua)) platform = 'iOS';
      else if (/Mac OS X|Macintosh/.test(ua)) platform = 'macOS';
      else if (/Linux/.test(ua)) platform = 'Linux';
      return [browser, platform].filter(Boolean).join(' · ') || ua.slice(0, 40);
    },

    async loadAccess() {
      if (!window.BoarAiAuth) return;
      try {
        const [settings, devices] = await Promise.all([
          window.BoarAiAuth.settings(),
          window.BoarAiAuth.sessions(),
        ]);
        this.accessTtlSeconds = (settings.ttl_options || []).map(Number);
        const preferred = String(settings.session_ttl_seconds || '');
        this.accessConfig.ttl = this.accessTtlSeconds.some((s) => String(s) === preferred)
          ? preferred
          : (this.accessTtlSeconds.length ? String(this.accessTtlSeconds[0]) : '');
        this.accessConfig.sliding = !!settings.sliding;
        this.accessDevices = devices;
      } catch (error) {
        console.warn('Failed to load access settings:', error);
      }
    },

    async saveAccessTtl() {
      if (!window.BoarAiAuth || !this.accessConfig.ttl) return;
      this.accessSaving = true;
      try {
        await window.BoarAiAuth.saveSettings({
          session_ttl_seconds: Number(this.accessConfig.ttl),
        });
        window.BoarAiStore?.actions?.success(
          this.t('settings.accessSaved') || 'Access settings saved',
        );
        await this.loadAccess();
      } catch (error) {
        window.BoarAiStore?.actions?.error(error.message || 'Save failed');
      } finally {
        this.accessSaving = false;
      }
    },

    async saveAccessSliding(value) {
      if (!window.BoarAiAuth) return;
      try {
        await window.BoarAiAuth.saveSettings({ sliding: !!value });
      } catch (error) {
        // Revert the optimistic toggle so the UI never lies about the policy.
        this.accessConfig.sliding = !value;
        window.BoarAiStore?.actions?.error(error.message || 'Save failed');
      }
    },

    async revokeDevice(device) {
      if (!window.BoarAiAuth || !device || device.current) return;
      const ok = await window.BoarAiStore?.actions?.confirm({
        title: this.t('settings.revokeDeviceTitle') || 'Revoke device',
        message: this.t('settings.revokeDeviceMessage')
          || 'This device will need the access key again.',
        confirmText: this.t('settings.revokeDeviceAction') || 'Revoke',
        type: 'danger',
      });
      if (!ok) return;
      try {
        await window.BoarAiAuth.revokeSession(device.id);
        await this.loadAccess();
      } catch (error) {
        window.BoarAiStore?.actions?.error(error.message || 'Revoke failed');
      }
    },

    async logoutDevice() {
      if (!window.BoarAiAuth) return;
      const ok = await window.BoarAiStore?.actions?.confirm({
        title: this.t('settings.logoutDeviceTitle') || 'Sign out this device',
        message: this.t('settings.logoutDeviceMessage')
          || 'You will need the access key to get back in.',
        confirmText: this.t('settings.logoutDeviceAction') || 'Sign out',
        type: 'warning',
      });
      if (!ok) return;
      // auth.js emits boarai-auth-required, which the router turns into /unlock.
      await window.BoarAiAuth.logout();
    },

    /**
     * Prepare a new key. Two-phase: nothing is applied here, so the operator
     * can take their time copying it. The old single-shot flow committed
     * immediately and the resulting `auth.revoked` navigated this page away,
     * destroying the only copy of the key — an unrecoverable lockout.
     */
    async rotateAccessKey() {
      if (!window.BoarAiAuth || this.accessRotating) return;
      this.accessRotating = true;
      try {
        const data = await window.BoarAiAuth.rotateKey();
        this.accessNewKey = data && data.key ? data.key : '';
        this.accessKeyCopied = false;
      } catch (error) {
        window.BoarAiStore?.actions?.error(error.message || 'Rotation failed');
      } finally {
        this.accessRotating = false;
      }
    },

    async copyNewKey() {
      if (!this.accessNewKey) return;
      try {
        await navigator.clipboard.writeText(this.accessNewKey);
        this.accessKeyCopied = true;
      } catch (_) {
        // Clipboard blocked (insecure context / permissions): the code block is
        // select-all, so tell the operator to copy manually instead of failing.
        window.BoarAiStore?.actions?.warning(
          this.t('settings.copyKeyManual') || 'Select the key and copy it manually',
        );
      }
    },

    /** Destructive step, gated behind an explicit "I saved it" confirmation. */
    async applyRotatedKey() {
      if (!window.BoarAiAuth || !this.accessNewKey || this.accessRotating) return;
      const ok = await window.BoarAiStore?.actions?.confirm({
        title: this.t('settings.applyKeyTitle') || 'Apply the new key',
        message: this.t('settings.applyKeyMessage')
          || 'Every device (including this one) is signed out and must enter the new key. This cannot be undone — the key is stored only as a hash.',
        confirmText: this.t('settings.applyKeyConfirm') || 'Apply',
        type: 'danger',
      });
      if (!ok) return;
      this.accessRotating = true;
      try {
        await window.BoarAiAuth.confirmRotation();
        this.accessNewKey = '';
        this.accessKeyCopied = false;
        // The server broadcast auth.revoked; the router now shows /unlock and
        // the operator signs back in with the key they just saved.
      } catch (error) {
        window.BoarAiStore?.actions?.error(error.message || 'Apply failed');
      } finally {
        this.accessRotating = false;
      }
    },

    async discardRotatedKey() {
      if (!window.BoarAiAuth) return;
      await window.BoarAiAuth.cancelRotation();
      this.accessNewKey = '';
      this.accessKeyCopied = false;
    },

    formatTime(isoStr) {
      if (!isoStr) return '';
      try {
        const d = new Date(isoStr);
        const h = String(d.getHours()).padStart(2, '0');
        const m = String(d.getMinutes()).padStart(2, '0');
        const s = String(d.getSeconds()).padStart(2, '0');
        return `${h}:${m}:${s}`;
      } catch (e) { return isoStr; }
    },
    
    async applyLanguage() {
      if (window.i18n) {
        try {
          await window.i18n.setLanguage(this.language);
        } catch (error) {
          console.error('Failed to set language:', error);
        }
      }
    },
    
    handleHomepageChange() {
      localStorage.setItem('boarai-homepage', this.homepage);
      window.BoarAiStore.actions.success(
        (this.t('settings.homepage') || 'Start Page') + ': ' + 
        (this.t('nav.' + this.homepage) || this.homepage)
      );
    },
    
    // ==================== Provider Management ====================
    
    async loadProviders() {
      try {
        const result = await window.BoarAiStore.api.getProviders();
        this.providers = result.providers || [];

        // Detail column: open the first provider on the initial load so the
        // right column is not empty. Later reloads keep the current selection.
        if (!this.providersLoaded) {
          this.providersLoaded = true;
          if (!this.providerFormMode && this.providers.length > 0) {
            this.startEditProvider(this.providers[0]);
          }
        }

        // Restore chat (default) model
        const chatS = this.capStates['chat'];
        if (chatS) {
          chatS.savedProvider = result.default_provider || '';
          chatS.savedModel = result.default_model || '';
          chatS.provider = result.default_provider || '';
          chatS.model = result.default_model || '';
          if (result.default_provider) {
            const dp = this.providers.find(p => p.name === result.default_provider);
            chatS.models = dp?.supported_models || [];
          }
        }
        
        // Restore capability models
        try {
          const capModels = await window.BoarAiStore.api.getCapabilityModels();
          for (const [capKey, cfg] of Object.entries(capModels)) {
            const s = this.capStates[capKey];
            if (s) {
              s.savedProvider = cfg.provider || '';
              s.savedModel = cfg.model || '';
              s.provider = cfg.provider || '';
              s.model = cfg.model || '';
              if (cfg.provider) {
                const dp = this.providers.find(p => p.name === cfg.provider);
                s.models = dp?.supported_models || [];
              }
            }
          }
        } catch (e) {
          console.warn('Failed to load capability models:', e);
        }
      } catch (error) {
        console.error('Failed to load providers:', error);
        window.BoarAiStore.actions.error(this.t('notifications.loadProvidersFailed'));
      }
    },
    
    _emptyProviderForm() {
      return {
        name: '',
        provider_type: 'openai',
        api_key: '',
        api_base: '',
        fetched_models: [],
        selected_models: [],
        fetching_models: false,
        manual_model: '',
        // Per-model context length (tokens). Keyed by model id; null/absent =
        // default, i.e. the catalog value applies at request time and nothing
        // is written to llm.yaml.
        model_context_windows: {},
        // Catalog window per model id, display only — never sent back.
        ctx_defaults: {},
        // Sticky flag: keep the custom input visible once "Custom" is chosen.
        ctx_custom: {},
        // Per-model capability toggles, persisted via saveProvider.
        model_caps: {},
      };
    },

    _normalizeModelId(m) {
      if (m == null) return '';
      if (typeof m === 'string') return m.trim();
      return String(m.id || m.name || m.model || '').trim();
    },

    /** Serialized subset of the form used to detect unsaved edits. */
    _snapshotForm() {
      return JSON.stringify({
        name: this.form.name || '',
        provider_type: this.form.provider_type || '',
        api_key: this.form.api_key || '',
        api_base: this.form.api_base || '',
        selected_models: [...(this.form.selected_models || [])].sort(),
        model_context_windows: this.form.model_context_windows || {},
        // Capability toggles are persisted now, so they count as edits.
        model_caps: Object.fromEntries(
          Object.keys(this.form.model_caps || {})
            .sort()
            .map((k) => [k, this.form.model_caps[k]])
        ),
      });
    },

    formDirty() {
      if (!this.providerFormMode) return false;
      return this._snapshotForm() !== this.formSnapshot;
    },

    /** Warn before throwing away unsaved edits. Resolves true = go ahead. */
    async _confirmDiscardForm() {
      if (!this.formDirty()) return true;
      return await window.BoarAiStore.actions.confirm({
        title: this.t('settings.discardChangesTitle'),
        message: this.t('settings.discardChangesMessage'),
        confirmText: this.t('common.discard'),
        cancelText: this.t('common.cancel'),
        type: 'warning',
      });
    },

    isProviderSelected(name) {
      return this.providerFormMode === 'edit' && this.editingProvider?.name === name;
    },

    /** Left column click → load that provider into the right column. */
    async selectProvider(provider) {
      if (this.isProviderSelected(provider.name)) {
        // Clicking the already-selected provider while a model panel is open
        // returns to the provider settings instead of being a no-op.
        this.backToProvider();
        return;
      }
      if (!(await this._confirmDiscardForm())) return;
      this.startEditProvider(provider);
    },

    async openAddProvider() {
      if (this.providerFormMode === 'new') return;
      if (!(await this._confirmDiscardForm())) return;
      this.editingProvider = null;
      this.form = this._emptyProviderForm();
      this.providerFormMode = 'new';
      this.detailMode = 'provider';
      this.modelDetail = null;
      this.formSnapshot = this._snapshotForm();
      this._scrollDetailIntoView();
    },

    /** Phones stack the columns, so bring the detail panel into view on select. */
    _scrollDetailIntoView() {
      if (!window.matchMedia || !window.matchMedia('(max-width: 1023px)').matches) return;
      this.$nextTick(() => {
        const el = this.$refs.providerDetail;
        if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    },

    /** Re-open the detail panel on `name` after a reload (no discard prompt). */
    _reselectProvider(name) {
      const found = this.providers.find((p) => p.name === name);
      if (found) this.startEditProvider(found);
      else this.cancelProviderForm();
    },

    startEditProvider(provider) {
      this.editingProvider = provider;
      const type =
        provider.provider_type ||
        (provider.api_base?.includes('anthropic') ? 'anthropic' : 'openai');
      const existing = (provider.supported_models || [])
        .map((m) => this._normalizeModelId(m))
        .filter(Boolean);
      // Include provider default_model if it was set but missing from the list
      const def = this._normalizeModelId(provider.default_model);
      if (def && !existing.includes(def)) existing.unshift(def);
      // Pre-populate only the windows the user explicitly set. The catalog
      // value arrives separately as a label for the "Default" choice, so a save
      // cannot turn a shipped number into a stored override.
      const cw = provider.context_windows || {};
      const modelCtx = {};
      const ctxCustom = {};
      existing.forEach((id) => {
        const raw = cw[id];
        if (raw == null || raw === '') return;
        const n = Number(raw);
        if (isNaN(n) || n <= 0) return;
        modelCtx[id] = n;
        const s = String(n);
        if (s !== '128000' && s !== '512000' && s !== '1000000') ctxCustom[id] = true;
      });
      // Seed capability toggles from what the server stored per model.
      const storedCaps = provider.capabilities || {};
      const seededCaps = {};
      existing.forEach((id) => {
        const src = storedCaps[id] || {};
        seededCaps[id] = {
          image: !!src.image,
          audio: !!src.audio,
          video: !!src.video,
          image_gen: !!src.image_gen,
          video_gen: !!src.video_gen,
        };
      });
      this.form = {
        name: provider.name,
        provider_type: type,
        api_key: '',
        api_base: provider.api_base || '',
        fetched_models: [],
        // Always surface existing models immediately (no fetch required)
        selected_models: [...existing],
        fetching_models: false,
        manual_model: '',
        model_context_windows: modelCtx,
        ctx_defaults: provider.context_window_defaults || {},
        ctx_custom: ctxCustom,
        model_caps: seededCaps,
      };
      this.providerFormMode = 'edit';
      this.detailMode = 'provider';
      this.modelDetail = null;
      this.formSnapshot = this._snapshotForm();
      this._scrollDetailIntoView();
    },

    cancelProviderForm() {
      this.providerFormMode = '';
      this.editingProvider = null;
      this.form = this._emptyProviderForm();
      this.formSnapshot = '';
      this.detailMode = 'provider';
      this.modelDetail = null;
    },

    /** Column 2 model row click → column 3 shows that model's settings. */
    selectModelDetail(modelId) {
      this.modelDetail = modelId;
      this.detailMode = 'model';
      if (!this.form.model_caps) this.form.model_caps = {};
      if (!this.form.model_caps[modelId]) {
        this.form.model_caps[modelId] = {
          image: false,
          audio: false,
          video: false,
          image_gen: false,
          video_gen: false,
        };
      }
      this._scrollDetailIntoView();
    },

    backToProvider() {
      this.detailMode = 'provider';
      this.modelDetail = null;
    },

    getModelCap(modelId, key) {
      const caps = (this.form.model_caps || {})[modelId];
      return !!(caps && caps[key]);
    },

    setModelCap(modelId, key, value) {
      if (!this.form.model_caps) this.form.model_caps = {};
      if (!this.form.model_caps[modelId]) this.form.model_caps[modelId] = {};
      this.form.model_caps[modelId][key] = !!value;
    },

    /** Capability definitions currently enabled for a model (row icons). */
    enabledCaps(modelId) {
      const src = (this.form.model_caps || {})[modelId] || {};
      return this.modelCaps.filter((mc) => src[mc.key]);
    },

    enabledCapsTitle(modelId) {
      return this.enabledCaps(modelId)
        .map((mc) => this.t('settings.mcap_' + mc.key) || mc.label)
        .join(' / ');
    },

    /** Short context-length label for the single-line model rows. */
    ctxBadge(m) {
      const mode = this.ctxMode(m);
      if (mode === 'custom') {
        // The custom input can be cleared mid-edit: never print null/undefined.
        const n = Number(this.form.model_context_windows[m]);
        if (Number.isFinite(n) && n > 0) return this._formatTokens(n);
        return this.t('settings.contextLengthCustom') || 'Custom';
      }
      if (mode) return this._formatTokens(Number(mode));
      return this.t('settings.contextLengthDefault') || 'Default';
    },

    addManualModel() {
      const id = this._normalizeModelId(this.form.manual_model);
      if (!id) return;
      if (!this.form.selected_models) this.form.selected_models = [];
      if (this.form.selected_models.includes(id)) {
        window.BoarAiStore.actions.error(
          this.t('settings.modelAlreadyAdded') || 'Model already in list'
        );
        return;
      }
      this.form.selected_models = [...this.form.selected_models, id];
      this.form.manual_model = '';
    },

    removeModel(modelId) {
      this.form.selected_models = (this.form.selected_models || []).filter(
        (m) => m !== modelId
      );
      if (this.form.model_context_windows) {
        delete this.form.model_context_windows[modelId];
      }
      if (this.form.ctx_custom) {
        delete this.form.ctx_custom[modelId];
      }
      if (this.form.model_caps) {
        delete this.form.model_caps[modelId];
      }
      // The removed model can no longer be shown in column 3.
      if (this.modelDetail === modelId) this.backToProvider();
    },

    // Per-model context length: derive the <select> mode from the stored value.
    // '' = default (unset), '128000'/'512000'/'1000000' = presets, 'custom' = manual.
    ctxMode(m) {
      if (this.form.ctx_custom && this.form.ctx_custom[m]) return 'custom';
      const v = this.form.model_context_windows
        ? this.form.model_context_windows[m]
        : null;
      if (v == null || v === '') return '';
      const s = String(v);
      if (s === '128000' || s === '512000' || s === '1000000') return s;
      return 'custom';
    },

    // "Default" means boar/config/models.yaml decides at request time and
    // nothing is stored in llm.yaml. Naming the number keeps that from reading
    // as "unknown"; a model the catalog lacks just says Default.
    ctxDefaultLabel(m) {
      const fallback = this.t('settings.contextLengthDefault') || 'Default';
      const n = Number((this.form.ctx_defaults || {})[m]);
      if (!Number.isFinite(n) || n <= 0) return fallback;
      return this.t('settings.contextLengthCatalog', { size: this._formatTokens(n) }) || fallback;
    },

    _formatTokens(value) {
      const n = Number(value);
      if (!Number.isFinite(n) || n <= 0) return String(value);
      if (n >= 1000000) return (n / 1000000).toFixed(n % 1000000 === 0 ? 0 : 1) + 'M';
      if (n >= 1000) return Math.round(n / 1000) + 'K';
      return String(n);
    },

    onCtxModeChange(m, mode) {
      if (!this.form.model_context_windows) this.form.model_context_windows = {};
      if (!this.form.ctx_custom) this.form.ctx_custom = {};
      if (mode === '') {
        this.form.model_context_windows[m] = null;
        this.form.ctx_custom[m] = false;
      } else if (mode === 'custom') {
        this.form.ctx_custom[m] = true;
        const cur = this.form.model_context_windows[m];
        if (cur == null || cur === '' || Number(cur) <= 0) {
          this.form.model_context_windows[m] = 262144;
        }
      } else {
        this.form.model_context_windows[m] = parseInt(mode, 10);
        this.form.ctx_custom[m] = false;
      }
    },

    onCtxCustomInput(m, val) {
      if (!this.form.model_context_windows) this.form.model_context_windows = {};
      const n = parseInt(val, 10);
      this.form.model_context_windows[m] = isNaN(n) ? null : n;
    },

    toggleFetchedModel(modelId) {
      const id = this._normalizeModelId(modelId);
      if (!id) return;
      const list = this.form.selected_models || [];
      if (list.includes(id)) {
        this.form.selected_models = list.filter((m) => m !== id);
      } else {
        this.form.selected_models = [...list, id];
      }
    },
    
    async fetchModels() {
      // Editing an existing provider: the backend reuses the stored api_key,
      // so no secret needs to round-trip to the browser.
      const editing = !!this.editingProvider;
      if (!this.form.api_base) return;
      if (!editing && !this.form.api_key) return;
      this.form.fetching_models = true;
      try {
        const params = {
          api_base: this.form.api_base,
          provider_type: this.form.provider_type,
        };
        if (editing) {
          params.provider_name = this.editingProvider.name;
        } else {
          params.api_key = this.form.api_key;
        }
        const result = await window.BoarAiStore.api.fetchProviderModels(params);
        const raw = result.models || [];
        // Normalize to {id, name, context_window} for the checkbox list. The
        // window is the catalog default for that id, shown as a label only.
        const defaults = { ...(this.form.ctx_defaults || {}) };
        this.form.fetched_models = raw
          .map((m) => {
            const id = this._normalizeModelId(m);
            if (!id) return null;
            const cw = typeof m === 'object' ? Number(m.context_window) : NaN;
            if (Number.isFinite(cw) && cw > 0) defaults[id] = cw;
            return { id, name: (typeof m === 'object' && m.name) || id };
          })
          .filter(Boolean);
        this.form.ctx_defaults = defaults;
        // Keep existing selected (manual + previous); do not wipe them
        if (this.form.fetched_models.length === 0) {
          window.BoarAiStore.actions.error(
            this.t('notifications.fetchModelsEmpty') || 'No models returned from API'
          );
        }
      } catch (error) {
        console.error('Failed to fetch models:', error);
        window.BoarAiStore.actions.error(
          error.message || this.t('notifications.fetchModelsFailed') || 'Failed to fetch models'
        );
      } finally {
        this.form.fetching_models = false;
      }
    },
    
    async saveProvider() {
      if (this.savingProvider || !this.providerFormMode) return;
      const models = [...new Set((this.form.selected_models || []).map((m) => this._normalizeModelId(m)).filter(Boolean))];
      // Prefer previously configured default if still selected; otherwise first model.
      let defaultModel = '';
      if (this.editingProvider) {
        const prev = this._normalizeModelId(this.editingProvider.default_model);
        if (prev && models.includes(prev)) defaultModel = prev;
      }
      if (!defaultModel && models.length) defaultModel = models[0];

      // Build per-model context length map (only explicitly-set positive values).
      // Models omitted here are cleared back to "Default" by the backend.
      const ctx = {};
      const mcw = this.form.model_context_windows || {};
      models.forEach((id) => {
        const v = mcw[id];
        if (v != null && v !== '' && Number(v) > 0) ctx[id] = Number(v);
      });

      // Per-model capability flags (persisted server-side per provider).
      const caps = {};
      const mcaps = this.form.model_caps || {};
      models.forEach((id) => {
        const src = mcaps[id] || {};
        caps[id] = {
          image: !!src.image,
          audio: !!src.audio,
          video: !!src.video,
          image_gen: !!src.image_gen,
          video_gen: !!src.video_gen,
        };
      });

      const editingName = this.editingProvider ? this.editingProvider.name : '';
      const newName = (this.form.name || '').trim();
      this.savingProvider = true;
      try {
        if (editingName) {
          // Update existing provider — always persist model list (including empty clear)
          const payload = {
            api_base: this.form.api_base || undefined,
            supported_models: models,
            default_model: defaultModel,
            context_windows: ctx,
            capabilities: caps,
          };
          if (this.form.api_key) payload.api_key = this.form.api_key;
          await window.BoarAiStore.api.updateProvider(editingName, payload);
          window.BoarAiStore.actions.success(this.t('notifications.providerUpdated'));
        } else {
          if (!newName) {
            window.BoarAiStore.actions.error(this.t('settings.providerName') + ' required');
            return;
          }
          if (!this.form.api_key?.trim()) {
            window.BoarAiStore.actions.error(this.t('settings.apiKey') + ' required');
            return;
          }
          await window.BoarAiStore.api.addProvider({
            name: newName,
            api_key: this.form.api_key,
            api_base: this.form.api_base,
            provider_type: this.form.provider_type,
            supported_models: models,
            default_model: defaultModel || undefined,
            context_windows: ctx,
            capabilities: caps,
          });
          window.BoarAiStore.actions.success(this.t('notifications.providerAdded'));
        }

        await this.loadProviders();
        // Stay on the provider that was just saved, refreshed from the server.
        this._reselectProvider(editingName || newName);
      } catch (error) {
        console.error('Failed to save provider:', error);
        window.BoarAiStore.actions.error(error.message || this.t('notifications.saveProviderFailed'));
      } finally {
        this.savingProvider = false;
      }
    },
    
    async deleteProvider(name) {
      const confirmed = await window.BoarAiStore.actions.confirm({
        title: this.t('notifications.deleteProviderTitle'),
        message: this.t('notifications.deleteProviderMessage', { name }),
        confirmText: this.t('common.delete'),
        type: 'danger',
      });
      
      if (!confirmed) return;
      
      try {
        await window.BoarAiStore.api.deleteProvider(name);
        window.BoarAiStore.actions.success(this.t('notifications.providerDeleted'));
        await this.loadProviders();
        // The deleted provider can no longer be shown: fall back to the first
        // remaining one, or close the detail panel when none is left.
        if (this.editingProvider?.name === name) {
          if (this.providers.length > 0) this.startEditProvider(this.providers[0]);
          else this.cancelProviderForm();
        }
      } catch (error) {
        console.error('Failed to delete provider:', error);
        window.BoarAiStore.actions.error(error.message || this.t('notifications.deleteProviderFailed'));
      }
    },
    
    async validateProvider(name) {
      try {
        const result = await window.BoarAiStore.api.validateProvider(name);
        if (result.valid) {
          window.BoarAiStore.actions.success(this.t('notifications.providerConnectionOk', { name }));
        } else {
          window.BoarAiStore.actions.error(this.t('notifications.providerValidationFailed', { name, error: result.error || this.t('common.unknownError') }));
        }
      } catch (error) {
        console.error('Failed to validate:', error);
        window.BoarAiStore.actions.error(error.message || this.t('notifications.validateProviderFailed'));
      }
    },
  }
};
