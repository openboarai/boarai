/**
 * Empty State Component
 * 
 * Displays a placeholder when there's no data to show.
 * Supports customizable icon, title, description, and action button.
 */

const EmptyStateComponent = {
  name: 'EmptyStateComponent',
  
  props: {
    icon: {
      type: String,
      default: 'inbox'
    },
    title: {
      type: String,
      default: 'No data'
    },
    description: {
      type: String,
      default: ''
    },
    actionText: {
      type: String,
      default: ''
    },
    actionIcon: {
      type: String,
      default: 'plus'
    },
    compact: {
      type: Boolean,
      default: false
    }
  },
  
  emits: ['action'],
  
  template: `
    <div :class="containerClasses">
      <div class="flex flex-col items-center text-center max-w-md">
        <!-- Icon -->
        <div :class="iconContainerClasses">
          <i :data-lucide="icon" :class="iconClasses"></i>
        </div>
        
        <!-- Title -->
        <h3 :class="titleClasses" class="font-medium text-gray-900 dark:text-white">
          {{ title }}
        </h3>
        
        <!-- Description -->
        <p v-if="description" class="text-gray-500 dark:text-gray-400 mt-1" :class="descriptionClasses">
          {{ description }}
        </p>
        
        <!-- Action Button -->
        <button v-if="actionText" 
                @click="$emit('action')"
                class="mt-4 inline-flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors"
                :class="actionClasses">
          <i :data-lucide="actionIcon" class="w-4 h-4"></i>
          <span>{{ actionText }}</span>
        </button>
      </div>
    </div>
  `,
  
  computed: {
    containerClasses() {
      return this.compact ? 'py-6' : 'py-12';
    },
    
    iconContainerClasses() {
      return this.compact 
        ? 'w-12 h-12 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center mb-3'
        : 'w-16 h-16 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center mb-4';
    },
    
    iconClasses() {
      return this.compact ? 'w-6 h-6 text-gray-400' : 'w-8 h-8 text-gray-400';
    },
    
    titleClasses() {
      return this.compact ? 'text-sm' : 'text-lg';
    },
    
    descriptionClasses() {
      return this.compact ? 'text-xs' : 'text-sm';
    },
    
    actionClasses() {
      return this.compact ? 'text-xs px-3 py-1.5' : 'text-sm';
    }
  }
  // Icons hydrate automatically via app.js MutationObserver.
};

/**
 * Empty State Presets
 * 
 * Pre-configured empty states for common scenarios.
 */

const EmptyStatePresets = {
  noExtensions: {
    icon: 'puzzle',
    title: 'No extensions installed',
    description: 'Browse the extension store to add new capabilities to your agent.',
    actionText: 'Browse Store',
    actionIcon: 'search'
  },
  
  noSessions: {
    icon: 'message-square',
    title: 'No conversations yet',
    description: 'Start a new chat to begin interacting with your AI agent.',
    actionText: 'New Session',
    actionIcon: 'plus'
  },
  
  noTasks: {
    icon: 'list-checks',
    title: 'No tasks created',
    description: 'Create tasks to organize your work and track progress.',
    actionText: 'Create Task',
    actionIcon: 'plus'
  },
  
  noResults: {
    icon: 'search-x',
    title: 'No results found',
    description: 'Try adjusting your search or filter criteria.'
  },
  
  error: {
    icon: 'alert-circle',
    title: 'Something went wrong',
    description: 'An error occurred while loading data. Please try again.',
    actionText: 'Retry',
    actionIcon: 'refresh-cw'
  }
};

// Export to global scope
window.EmptyStateComponent = EmptyStateComponent;
window.EmptyStatePresets = EmptyStatePresets;
