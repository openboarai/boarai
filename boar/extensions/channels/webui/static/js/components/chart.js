/**
 * Chart Components
 * 
 * Simple SVG-based chart components for data visualization.
 * Supports line chart, bar chart, and donut chart.
 */

/**
 * Line Chart Component
 * 
 * Displays data as a line graph with optional area fill.
 */
let lineChartUid = 0;

const LineChartComponent = {
  name: 'LineChartComponent',

  props: {
    data: {
      type: Array,
      required: true,
      // Array of { label: string, value: number }
    },
    height: {
      type: Number,
      default: 200
    },
    color: {
      type: String,
      default: ''
    },
    showArea: {
      type: Boolean,
      default: true
    },
    showGrid: {
      type: Boolean,
      default: true
    },
    showLabels: {
      type: Boolean,
      default: true
    },
    // Suffix appended to formatted values (e.g. '%')
    unit: {
      type: String,
      default: ''
    },
    // Optional custom value formatter: (value) => string
    formatter: {
      type: Function,
      default: null
    }
  },

  template: `
    <div class="w-full relative" ref="wrap">
      <svg :viewBox="'0 0 ' + width + ' ' + height" class="w-full block" :style="{ height: height + 'px' }"
           @pointermove="onPointerMove" @pointerleave="activeIndex = -1">
        <defs>
          <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" :stop-color="resolvedColor" stop-opacity="0.25" />
            <stop offset="100%" :stop-color="resolvedColor" stop-opacity="0.02" />
          </linearGradient>
        </defs>

        <!-- Horizontal grid + Y-axis tick labels -->
        <g v-if="showGrid">
          <g v-for="(tick, i) in yTicks" :key="'yt-' + i">
            <line :x1="pad.left" :x2="width - pad.right"
                  :y1="tick.y" :y2="tick.y"
                  stroke="currentColor" stroke-width="1"
                  :class="i === yTicks.length - 1 ? 'text-gray-300 dark:text-gray-600' : 'text-gray-200 dark:text-gray-700'"
                  :stroke-dasharray="i === yTicks.length - 1 ? null : '3 3'" />
            <text :x="pad.left - 6" :y="tick.y + 3" text-anchor="end"
                  class="text-[10px] fill-gray-400 dark:fill-gray-500">{{ tick.label }}</text>
          </g>
        </g>

        <!-- Area fill (gradient) -->
        <path v-if="showArea && points.length > 1"
              :d="areaPath"
              :fill="'url(#' + gradId + ')'" />

        <!-- Line -->
        <path v-if="points.length > 1"
              :d="linePath"
              :stroke="resolvedColor"
              stroke-width="2"
              fill="none"
              stroke-linecap="round"
              stroke-linejoin="round" />

        <!-- Hover guide line -->
        <line v-if="activePoint"
              :x1="activePoint.x" :x2="activePoint.x"
              :y1="pad.top" :y2="pad.top + chartHeight"
              :stroke="resolvedColor" stroke-width="1" stroke-dasharray="3 3" opacity="0.45" />

        <!-- Data points -->
        <circle v-for="p in points" :key="'point-' + p.key"
                :cx="p.x" :cy="p.y"
                :r="activeIndex === p.index ? 4.5 : 3"
                :fill="resolvedColor"
                :stroke="activeIndex === p.index ? '#ffffff' : 'none'"
                :stroke-width="activeIndex === p.index ? 1.5 : 0"
                class="dark:stroke-gray-800" />

        <!-- X labels -->
        <g v-if="showLabels && points.length > 0">
          <text v-for="p in points" :key="'label-' + p.key"
                :x="p.x"
                :y="height - 6"
                text-anchor="middle"
                class="text-[10px] fill-gray-500">
            {{ p.label }}
          </text>
        </g>
      </svg>

      <!-- Hover tooltip -->
      <div v-if="activePoint"
           class="absolute pointer-events-none z-10 px-2 py-1 rounded-md bg-gray-900/90 dark:bg-gray-700/95 text-white text-[11px] leading-tight shadow-lg whitespace-nowrap"
           :style="tooltipStyle">
        <div class="opacity-70">{{ activePoint.label }}</div>
        <div class="font-semibold">{{ formatValue(activePoint.value) }}</div>
      </div>
    </div>
  `,

  data() {
    return {
      width: 300,
      activeIndex: -1,
      gradId: 'lc-grad-' + (++lineChartUid),
      _ro: null
    };
  },

  mounted() {
    // Responsive: viewBox width tracks the rendered width 1:1 so the plot
    // fills the card instead of being letterboxed by preserveAspectRatio.
    this.width = Math.max(this.$el.clientWidth || 300, 120);
    if (typeof ResizeObserver === 'function') {
      this._ro = new ResizeObserver((entries) => {
        const w = entries[0] && entries[0].contentRect ? entries[0].contentRect.width : 0;
        if (w > 0) this.width = Math.max(Math.round(w), 120);
      });
      this._ro.observe(this.$el);
    }
  },

  beforeUnmount() {
    if (this._ro) {
      this._ro.disconnect();
      this._ro = null;
    }
  },

  computed: {
    resolvedColor() {
      return this.color || (window.UiKit ? window.UiKit.primaryHex() : '#6366f1');
    },

    pad() {
      return { top: 10, right: 10, bottom: this.showLabels ? 22 : 8, left: 38 };
    },

    chartWidth() {
      return Math.max(this.width - this.pad.left - this.pad.right, 10);
    },

    chartHeight() {
      return Math.max(this.height - this.pad.top - this.pad.bottom, 10);
    },

    niceMax() {
      if (this.data.length === 0) return 100;
      const m = Math.max(...this.data.map(d => d.value), 0);
      if (m <= 0) return this.unit === '%' ? 100 : 10;
      const pow = Math.pow(10, Math.floor(Math.log10(m)));
      const n = m / pow;
      const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
      return nice * pow;
    },

    yTicks() {
      const ticks = [];
      const steps = 4;
      for (let i = 0; i <= steps; i++) {
        const value = this.niceMax * (1 - i / steps);
        ticks.push({
          y: this.pad.top + (this.chartHeight * i) / steps,
          label: this.formatValue(value)
        });
      }
      return ticks;
    },

    points() {
      if (this.data.length === 0) return [];

      const stepX = this.chartWidth / Math.max(this.data.length - 1, 1);

      const keyCounts = new Map();
      return this.data.map((d, i) => {
        const baseKey = String(d.id ?? d.key ?? d.label ?? i);
        const occurrence = keyCounts.get(baseKey) || 0;
        keyCounts.set(baseKey, occurrence + 1);
        return {
          key: occurrence === 0 ? baseKey : `${baseKey}-${occurrence}`,
          index: i,
          label: d.label,
          value: d.value,
          x: this.pad.left + (i * stepX),
          y: this.pad.top + this.chartHeight - (d.value / this.niceMax * this.chartHeight)
        };
      });
    },

    activePoint() {
      if (this.activeIndex < 0 || this.activeIndex >= this.points.length) return null;
      return this.points[this.activeIndex];
    },

    tooltipStyle() {
      const p = this.activePoint;
      if (!p) return {};
      const w = 84;
      let left = p.x - w / 2;
      left = Math.min(Math.max(left, 0), Math.max(this.width - w, 0));
      const top = p.y > 56 ? p.y - 52 : p.y + 14;
      return { left: left + 'px', top: top + 'px', width: w + 'px' };
    },

    linePath() {
      if (this.points.length === 0) return '';

      return this.points.map((p, i) => {
        const cmd = i === 0 ? 'M' : 'L';
        return `${cmd} ${p.x} ${p.y}`;
      }).join(' ');
    },

    areaPath() {
      if (this.points.length === 0) return '';

      const line = this.linePath;
      const lastPoint = this.points[this.points.length - 1];
      const firstPoint = this.points[0];
      const bottomY = this.pad.top + this.chartHeight;

      return `${line} L ${lastPoint.x} ${bottomY} L ${firstPoint.x} ${bottomY} Z`;
    }
  },

  methods: {
    formatValue(v) {
      if (typeof this.formatter === 'function') return String(this.formatter(v));
      const abs = Math.abs(v);
      let out;
      if (abs >= 1e6) out = (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
      else if (abs >= 1e3) out = (v / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
      else if (Number.isInteger(v)) out = String(v);
      else out = v.toFixed(1);
      return out + this.unit;
    },

    onPointerMove(e) {
      if (this.points.length === 0) return;
      const svg = e.currentTarget;
      const rect = svg.getBoundingClientRect();
      // viewBox width == rendered width (scale 1), so client px map directly
      const x = ((e.clientX - rect.left) / rect.width) * this.width;
      let best = 0;
      let bestDist = Infinity;
      for (let i = 0; i < this.points.length; i++) {
        const dist = Math.abs(this.points[i].x - x);
        if (dist < bestDist) {
          bestDist = dist;
          best = i;
        }
      }
      this.activeIndex = best;
    }
  }
};

/**
 * Bar Chart Component
 * 
 * Displays data as vertical bars.
 */
const BarChartComponent = {
  name: 'BarChartComponent',
  
  props: {
    data: {
      type: Array,
      required: true,
      // Array of { label: string, value: number, color?: string }
    },
    height: {
      type: Number,
      default: 200
    },
    color: {
      type: String,
      default: ''
    },
    showLabels: {
      type: Boolean,
      default: true
    },
    showValues: {
      type: Boolean,
      default: false
    }
  },

  template: `
    <div class="w-full">
      <svg :viewBox="'0 0 ' + width + ' ' + height" class="w-full" :style="{ height: height + 'px' }">
        <!-- Bars -->
        <g v-for="bar in bars" :key="'bar-' + bar.key">
          <rect :x="bar.x"
                :y="bar.y"
                :width="bar.width"
                :height="bar.height"
                :fill="bar.color || resolvedColor"
                rx="4"
                class="transition-all duration-200 hover:opacity-80" />
          
          <!-- Value label -->
          <text v-if="showValues"
                :x="bar.x + bar.width / 2"
                :y="bar.y - 5"
                text-anchor="middle"
                class="text-[10px] fill-gray-600 dark:fill-gray-400 font-medium">
            {{ bar.value }}
          </text>
        </g>
        
        <!-- Labels -->
        <g v-if="showLabels">
          <text v-for="bar in bars" :key="'label-' + bar.key"
                :x="bar.x + bar.width / 2"
                :y="height - 5"
                text-anchor="middle"
                class="text-[10px] fill-gray-500">
            {{ bar.label }}
          </text>
        </g>
      </svg>
    </div>
  `,
  
  data() {
    return {
      width: 300,
      padding: 20
    };
  },
  
  computed: {
    resolvedColor() {
      return this.color || (window.UiKit ? window.UiKit.primaryHex() : '#6366f1');
    },

    chartWidth() {
      return this.width - (this.padding * 2);
    },
    
    chartHeight() {
      return this.height - (this.padding * 2) - (this.showLabels ? 20 : 0) - (this.showValues ? 20 : 0);
    },
    
    maxValue() {
      if (this.data.length === 0) return 100;
      return Math.max(...this.data.map(d => d.value), 1);
    },
    
    barWidth() {
      if (this.data.length === 0) return 0;
      return (this.chartWidth / this.data.length) * 0.7;
    },
    
    barGap() {
      if (this.data.length === 0) return 0;
      return (this.chartWidth / this.data.length) * 0.3;
    },
    
    bars() {
      const keyCounts = new Map();
      return this.data.map((d, i) => {
        const barHeight = (d.value / this.maxValue) * this.chartHeight;
        const baseKey = String(d.id ?? d.key ?? d.label ?? i);
        const occurrence = keyCounts.get(baseKey) || 0;
        keyCounts.set(baseKey, occurrence + 1);
        return {
          key: occurrence === 0 ? baseKey : `${baseKey}-${occurrence}`,
          label: d.label,
          value: d.value,
          color: d.color,
          x: this.padding + (i * (this.barWidth + this.barGap)) + (this.barGap / 2),
          y: this.padding + (this.showValues ? 20 : 0) + (this.chartHeight - barHeight),
          width: this.barWidth,
          height: barHeight
        };
      });
    }
  }
};

/**
 * Donut Chart Component
 * 
 * Displays data as a donut/pie chart.
 */
const DonutChartComponent = {
  name: 'DonutChartComponent',
  
  props: {
    data: {
      type: Array,
      required: true,
      // Array of { label: string, value: number, color: string }
    },
    size: {
      type: Number,
      default: 160
    },
    thickness: {
      type: Number,
      default: 30
    },
    showLegend: {
      type: Boolean,
      default: true
    },
    showLabels: {
      type: Boolean,
      default: true
    }
  },
  
  template: `
    <div class="flex items-center gap-6">
      <!-- Chart -->
      <div class="flex-shrink-0">
        <svg :width="size" :height="size" :viewBox="'0 0 ' + size + ' ' + size">
          <!-- Background circle -->
          <circle :cx="center" 
                  :cy="center" 
                  :r="radius"
                  fill="none"
                  :stroke-width="thickness"
                  class="stroke-gray-200 dark:stroke-gray-700" />
          
          <!-- Data segments -->
          <circle v-for="segment in segments" :key="'segment-' + segment.key"
                  :cx="center"
                  :cy="center"
                  :r="radius"
                  fill="none"
                  :stroke="segment.color"
                  :stroke-width="thickness"
                  :stroke-dasharray="segment.dashArray"
                  :stroke-dashoffset="segment.dashOffset"
                  :transform="'rotate(-90 ' + center + ' ' + center + ')'"
                  class="transition-all duration-300" />
          
          <!-- Center text -->
          <text v-if="showLabels"
                :x="center" 
                :y="center"
                text-anchor="middle"
                dominant-baseline="middle"
                class="text-lg font-bold fill-gray-900 dark:fill-white">
            {{ totalValue }}
          </text>
          <text v-if="showLabels"
                :x="center" 
                :y="center + 16"
                text-anchor="middle"
                dominant-baseline="middle"
                class="text-[10px] fill-gray-500">
            Total
          </text>
        </svg>
      </div>
      
      <!-- Legend -->
      <div v-if="showLegend" class="flex-1 min-w-0">
        <div class="space-y-2">
          <div v-for="(item, index) in data" :key="'legend-' + (item.id ?? item.key ?? item.label ?? index) + '-' + index"
               class="flex items-center gap-2">
            <div class="w-3 h-3 rounded-full flex-shrink-0" :style="{ backgroundColor: item.color }"></div>
            <span class="text-sm text-gray-700 dark:text-gray-300 truncate">{{ item.label }}</span>
            <span class="text-sm font-medium text-gray-900 dark:text-white ml-auto">{{ item.value }}</span>
          </div>
        </div>
      </div>
    </div>
  `,
  
  computed: {
    center() {
      return this.size / 2;
    },
    
    radius() {
      return (this.size - this.thickness) / 2;
    },
    
    circumference() {
      return 2 * Math.PI * this.radius;
    },
    
    totalValue() {
      return this.data.reduce((sum, d) => sum + d.value, 0);
    },
    
    segments() {
      if (this.totalValue === 0) return [];
      
      let currentOffset = 0;
      const keyCounts = new Map();
      
      return this.data.map((d, index) => {
        const percentage = d.value / this.totalValue;
        const baseKey = String(d.id ?? d.key ?? d.label ?? index);
        const occurrence = keyCounts.get(baseKey) || 0;
        keyCounts.set(baseKey, occurrence + 1);
        const dashLength = this.circumference * percentage;
        const gapLength = this.circumference - dashLength;
        
        const segment = {
          key: occurrence === 0 ? baseKey : `${baseKey}-${occurrence}`,
          dashArray: `${dashLength} ${gapLength}`,
          dashOffset: -currentOffset,
          color: d.color
        };
        
        currentOffset += dashLength;
        
        return segment;
      });
    }
  }
};

/**
 * Sparkline Component
 * 
 * Small inline chart for quick data visualization.
 */
const SparklineComponent = {
  name: 'SparklineComponent',
  
  props: {
    data: {
      type: Array,
      required: true
    },
    width: {
      type: Number,
      default: 100
    },
    height: {
      type: Number,
      default: 30
    },
    color: {
      type: String,
      default: ''
    }
  },

  template: `
    <svg :viewBox="'0 0 ' + width + ' ' + height" :width="width" :height="height">
      <path :d="path"
            :stroke="resolvedColor"
            stroke-width="1.5"
            fill="none"
            stroke-linecap="round"
            stroke-linejoin="round" />
    </svg>
  `,
  
  computed: {
    resolvedColor() {
      return this.color || (window.UiKit ? window.UiKit.primaryHex() : '#6366f1');
    },

    points() {
      if (this.data.length === 0) return [];
      
      const max = Math.max(...this.data, 1);
      const stepX = this.width / Math.max(this.data.length - 1, 1);
      
      return this.data.map((v, i) => ({
        x: i * stepX,
        y: this.height - (v / max * this.height)
      }));
    },
    
    path() {
      return this.points.map((p, i) => {
        const cmd = i === 0 ? 'M' : 'L';
        return `${cmd} ${p.x} ${p.y}`;
      }).join(' ');
    }
  }
};

// Export to global scope
window.LineChartComponent = LineChartComponent;
window.BarChartComponent = BarChartComponent;
window.DonutChartComponent = DonutChartComponent;
window.SparklineComponent = SparklineComponent;
