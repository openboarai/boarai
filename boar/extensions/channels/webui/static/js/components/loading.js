/**
 * Loading Component
 * 
 * Global loading overlay with spinner and customizable text.
 * Can be used as overlay or inline component.
 */

const LoadingComponent = {
  name: 'LoadingComponent',
  
  props: {
    overlay: {
      type: Boolean,
      default: false
    },
    text: {
      type: String,
      default: ''
    },
    size: {
      type: String,
      default: 'md' // sm, md, lg
    }
  },
  
  template: `
    <div :class="containerClasses">
      <div class="flex flex-col items-center gap-3">
        <!-- Spinner -->
        <div :class="spinnerClasses">
          <svg class="animate-spin" :class="svgClasses" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
        </div>
        
        <!-- Text -->
        <p v-if="displayText" :class="textClasses" class="text-gray-600 dark:text-gray-400 font-medium">
          {{ displayText }}
        </p>
      </div>
    </div>
  `,
  
  computed: {
    displayText() {
      return this.text || (window.BoarAiStore ? window.BoarAiStore.state.loadingText : '');
    },
    
    containerClasses() {
      if (this.overlay) {
        return 'fixed inset-0 z-50 flex items-center justify-center bg-white/80 dark:bg-gray-900/80 backdrop-blur-sm';
      }
      return 'flex items-center justify-center p-8';
    },
    
    spinnerClasses() {
      const sizes = {
        sm: 'w-6 h-6',
        md: 'w-10 h-10',
        lg: 'w-16 h-16'
      };
      return sizes[this.size] || sizes.md;
    },
    
    svgClasses() {
      return 'text-primary';
    },
    
    textClasses() {
      const sizes = {
        sm: 'text-xs',
        md: 'text-sm',
        lg: 'text-base'
      };
      return sizes[this.size] || sizes.md;
    }
  }
};

/**
 * Loading Overlay Component
 * 
 * Full-screen loading overlay controlled by store state.
 */

const LoadingOverlayComponent = {
  name: 'LoadingOverlayComponent',
  
  template: `
    <transition name="fade">
      <div v-if="isLoading" 
           class="fixed inset-0 z-[90] flex items-center justify-center bg-white/80 dark:bg-gray-900/80 backdrop-blur-sm">
        <div class="flex flex-col items-center gap-4 p-8 bg-white dark:bg-gray-800 rounded-2xl shadow-xl">
          <!-- Spinner -->
          <div class="w-12 h-12">
            <svg class="animate-spin text-primary" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
          </div>
          
          <!-- Text -->
          <p v-if="loadingText" class="text-sm font-medium text-gray-600 dark:text-gray-400">
            {{ loadingText }}
          </p>
        </div>
      </div>
    </transition>
  `,
  
  computed: {
    isLoading() {
      return window.BoarAiStore ? window.BoarAiStore.state.isLoading : false;
    },
    
    loadingText() {
      return window.BoarAiStore ? window.BoarAiStore.state.loadingText : '';
    }
  }
};

// Export to global scope
window.LoadingComponent = LoadingComponent;
window.LoadingOverlayComponent = LoadingOverlayComponent;
