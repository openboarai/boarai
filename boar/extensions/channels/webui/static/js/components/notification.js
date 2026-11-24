/**
 * Notification Component
 * 
 * Global notification/toast system for displaying messages.
 * Supports success, error, warning, and info notification types.
 */

const NotificationComponent = {
  name: 'NotificationComponent',
  
  template: `
    <div class="fixed top-4 right-4 z-[1050] flex flex-col gap-2 max-w-sm">
      <transition-group name="notification">
        <div v-for="notification in notifications" 
             :key="notification.id"
             v-show="notification.show"
             class="flex items-start gap-3 p-4 rounded-lg shadow-lg border backdrop-blur-sm"
             :class="notificationClasses(notification.type)">
          <!-- Icon -->
          <div class="flex-shrink-0 mt-0.5">
            <i :data-lucide="notificationIcon(notification.type)" class="w-5 h-5"></i>
          </div>
          
          <!-- Content -->
          <div class="flex-1 min-w-0">
            <p class="text-sm font-medium">{{ notification.message }}</p>
          </div>
          
          <!-- Close button -->
          <button @click="dismiss(notification.id)" 
                  class="flex-shrink-0 p-1 rounded-md hover:bg-black/10 dark:hover:bg-white/10 transition-colors">
            <i data-lucide="x" class="w-4 h-4"></i>
          </button>
        </div>
      </transition-group>
    </div>
  `,
  
  computed: {
    notifications() {
      return window.BoarAiStore ? window.BoarAiStore.state.notifications : [];
    }
  },
  
  methods: {
    notificationClasses(type) {
      const base = 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700';
      const typeClasses = {
        success: 'border-l-4 border-l-green-500 text-green-700 dark:text-green-400',
        error: 'border-l-4 border-l-red-500 text-red-700 dark:text-red-400',
        warning: 'border-l-4 border-l-yellow-500 text-yellow-700 dark:text-yellow-400',
        info: 'border-l-4 border-l-blue-500 text-blue-700 dark:text-blue-400'
      };
      return `${base} ${typeClasses[type] || typeClasses.info}`;
    },
    
    notificationIcon(type) {
      const icons = {
        success: 'check-circle-2',
        error: 'x-circle',
        warning: 'alert-triangle',
        info: 'info'
      };
      return icons[type] || icons.info;
    },
    
    dismiss(id) {
      if (window.BoarAiStore) {
        window.BoarAiStore.actions.dismissNotification(id);
      }
    }
  },
  
  // Icons hydrate automatically via app.js MutationObserver (incl. dynamic
  // :data-lucide swaps on notification type change).
};

// Export to global scope
window.NotificationComponent = NotificationComponent;
