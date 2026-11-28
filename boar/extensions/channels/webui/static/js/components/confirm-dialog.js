/**
 * Confirm Dialog Component
 * 
 * Custom confirmation dialog to replace native confirm().
 * Supports different types (warning, danger, info) with appropriate styling.
 */

const ConfirmDialogComponent = {
  name: 'ConfirmDialogComponent',
  
  template: `
    <transition name="fade">
      <div v-if="show" 
           class="fixed inset-0 z-[100] flex items-center justify-center p-4"
           @click.self="cancel">
        <!-- Backdrop -->
        <div class="absolute inset-0 bg-black/50 backdrop-blur-sm"></div>
        
        <!-- Dialog -->
        <div class="relative w-full max-w-md bg-white dark:bg-gray-800 rounded-2xl shadow-xl overflow-hidden animate-slide-up">
          <!-- Header -->
          <div class="flex items-start gap-4 p-6 pb-4">
            <!-- Icon -->
            <div :class="iconContainerClasses">
              <i :data-lucide="iconName" class="w-6 h-6"></i>
            </div>
            
            <!-- Content -->
            <div class="flex-1 min-w-0">
              <h3 class="text-lg font-semibold text-gray-900 dark:text-white">
                {{ title }}
              </h3>
              <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
                {{ message }}
              </p>
            </div>
          </div>
          
          <!-- Actions -->
          <div class="flex items-center justify-end gap-3 px-6 py-4 bg-gray-50 dark:bg-gray-700/50">
            <button @click="cancel"
                    class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-600 transition-colors">
              {{ cancelText }}
            </button>
            <button @click="confirm"
                    :class="confirmButtonClasses"
                    class="px-4 py-2 text-sm font-medium rounded-lg transition-colors">
              {{ confirmText }}
            </button>
          </div>
        </div>
      </div>
    </transition>
  `,
  
  computed: {
    dialogState() {
      return window.BoarAiStore ? window.BoarAiStore.state.confirmDialog : {
        show: false,
        title: '',
        message: '',
        confirmText: 'Confirm',
        cancelText: 'Cancel',
        type: 'warning',
        onConfirm: null,
        onCancel: null
      };
    },
    
    show() {
      return this.dialogState.show;
    },
    
    title() {
      return this.dialogState.title;
    },
    
    message() {
      return this.dialogState.message;
    },
    
    confirmText() {
      return this.dialogState.confirmText;
    },
    
    cancelText() {
      return this.dialogState.cancelText;
    },
    
    type() {
      return this.dialogState.type;
    },
    
    iconName() {
      const icons = {
        warning: 'alert-triangle',
        danger: 'trash-2',
        info: 'info',
        question: 'help-circle'
      };
      return icons[this.type] || icons.warning;
    },
    
    iconContainerClasses() {
      const classes = {
        warning: 'w-12 h-12 rounded-xl bg-sun-100 dark:bg-sun-900/30 flex items-center justify-center text-sun-700 dark:text-sun-300',
        danger: 'w-12 h-12 rounded-xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center text-red-600 dark:text-red-300',
        info: 'w-12 h-12 rounded-xl bg-primary-100 dark:bg-primary-900/30 flex items-center justify-center text-primary dark:text-primary-300',
        question: 'w-12 h-12 rounded-xl bg-gray-100 dark:bg-gray-700 flex items-center justify-center text-gray-600 dark:text-gray-300'
      };
      return classes[this.type] || classes.warning;
    },
    
    confirmButtonClasses() {
      const classes = {
        warning: 'bg-sun-100 hover:bg-sun-200 text-sun-900 dark:bg-sun-900 dark:hover:bg-sun-800 dark:text-sun-100',
        danger: 'bg-red-600 hover:bg-red-700 text-white dark:bg-red-700 dark:hover:bg-red-800',
        info: 'bg-primary hover:bg-primary-dark text-white',
        question: 'bg-primary hover:bg-primary-dark text-white'
      };
      return classes[this.type] || classes.warning;
    }
  },
  
  methods: {
    confirm() {
      if (this.dialogState.onConfirm) {
        this.dialogState.onConfirm();
      }
    },
    
    cancel() {
      if (this.dialogState.onCancel) {
        this.dialogState.onCancel();
      }
    }
  }
  // Icons hydrate automatically via app.js MutationObserver.
};

// Export to global scope
window.ConfirmDialogComponent = ConfirmDialogComponent;
