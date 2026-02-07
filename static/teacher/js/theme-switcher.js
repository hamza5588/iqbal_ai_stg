/**
 * Global Theme Color Switcher
 * Allows dynamic color changes across the entire application
 */

class ThemeSwitcher {
    constructor() {
        this.colorKey = 'iqbal-primary-color';
        this.accentKey = 'iqbal-accent-color';
        this.init();
    }

    // Default color palettes
    colorPalettes = {
        blue: {
            primary: '#0ea5e9',
            secondary: '#0284c7',
            tertiary: '#3b82f6',
            light: '#e0f2fe',
            lighter: '#f0f9ff',
            name: 'Sky Blue'
        },
        indigo: {
            primary: '#6366f1',
            secondary: '#4f46e5',
            tertiary: '#818cf8',
            light: '#e0e7ff',
            lighter: '#f0f4ff',
            name: 'Indigo'
        },
        purple: {
            primary: '#a855f7',
            secondary: '#9333ea',
            tertiary: '#d8b4fe',
            light: '#f3e8ff',
            lighter: '#faf5ff',
            name: 'Purple'
        },
        emerald: {
            primary: '#10b981',
            secondary: '#059669',
            tertiary: '#6ee7b7',
            light: '#d1fae5',
            lighter: '#f0fdf4',
            name: 'Emerald'
        },
        rose: {
            primary: '#f43f5e',
            secondary: '#e11d48',
            tertiary: '#fb7185',
            light: '#ffe4e6',
            lighter: '#fff1f2',
            name: 'Rose'
        },
        orange: {
            primary: '#f97316',
            secondary: '#ea580c',
            tertiary: '#fb923c',
            light: '#fed7aa',
            lighter: '#fff7ed',
            name: 'Orange'
        },
        cyan: {
            primary: '#06b6d4',
            secondary: '#0891b2',
            tertiary: '#22d3ee',
            light: '#cffafe',
            lighter: '#ecf9ff',
            name: 'Cyan'
        },
        teal: {
            primary: '#14b8a6',
            secondary: '#0d9488',
            tertiary: '#2dd4bf',
            light: '#ccfbf1',
            lighter: '#f0fdfa',
            name: 'Teal'
        }
    };

    init() {
        this.loadSavedTheme();
        this.setupColorPicker();
        this.setupThemeButtons();
    }

    // Load saved theme from localStorage
    loadSavedTheme() {
        const savedPrimary = localStorage.getItem(this.colorKey);
        if (savedPrimary) {
            this.setPrimaryColor(savedPrimary);
        }
    }

    // Set primary color globally
    setPrimaryColor(colorHex) {
        const root = document.documentElement;
        root.style.setProperty('--primary-color', colorHex);
        localStorage.setItem(this.colorKey, colorHex);
        this.updateColorDependencies(colorHex);
    }

    // Update dependent colors based on primary
    updateColorDependencies(primaryHex) {
        const root = document.documentElement;
        
        // Convert hex to RGB for opacity variations
        const rgb = this.hexToRgb(primaryHex);
        if (rgb) {
            root.style.setProperty('--primary-rgb', `${rgb.r}, ${rgb.g}, ${rgb.b}`);
        }

        // Update CSS variables for Tailwind override
        root.style.setProperty('--primary-500', primaryHex);
        root.style.setProperty('--primary-600', this.adjustBrightness(primaryHex, -20));
        root.style.setProperty('--primary-700', this.adjustBrightness(primaryHex, -40));
        root.style.setProperty('--primary-400', this.adjustBrightness(primaryHex, 20));
        root.style.setProperty('--primary-300', this.adjustBrightness(primaryHex, 40));
        root.style.setProperty('--primary-100', this.adjustBrightness(primaryHex, 80));
        root.style.setProperty('--primary-50', this.adjustBrightness(primaryHex, 95));

        // Trigger re-render of dynamic elements
        this.notifyColorChange(primaryHex);
    }

    // Setup color picker input
    setupColorPicker() {
        const pickerContainer = document.getElementById('theme-color-picker');
        if (!pickerContainer) return;

        const html = `
            <div class="flex items-center gap-2 p-3 bg-white/50 backdrop-blur-sm rounded-lg border border-gray-200">
                <label class="text-sm font-medium text-gray-700">Theme Color:</label>
                <input 
                    type="color" 
                    id="primaryColorInput" 
                    class="w-10 h-10 rounded cursor-pointer border border-gray-300"
                    value="${localStorage.getItem(this.colorKey) || '#0ea5e9'}"
                >
                <span id="colorLabel" class="text-xs text-gray-600">Custom</span>
            </div>
        `;
        pickerContainer.innerHTML = html;

        const colorInput = document.getElementById('primaryColorInput');
        if (colorInput) {
            colorInput.addEventListener('change', (e) => {
                this.setPrimaryColor(e.target.value);
                document.getElementById('colorLabel').textContent = 'Custom Color';
            });
        }
    }

    // Setup preset theme buttons
    setupThemeButtons() {
        const buttonContainer = document.getElementById('theme-buttons');
        if (!buttonContainer) return;

        const html = `
            <div class="flex flex-wrap gap-2">
                ${Object.entries(this.colorPalettes).map(([key, palette]) => `
                    <button 
                        class="theme-preset-btn px-3 py-2 rounded-lg text-xs font-medium transition-all duration-200 border-2"
                        data-color="${palette.primary}"
                        title="${palette.name}"
                        style="background-color: ${palette.light}; border-color: ${palette.primary}; color: ${palette.primary};"
                    >
                        ${palette.name}
                    </button>
                `).join('')}
            </div>
        `;
        buttonContainer.innerHTML = html;

        document.querySelectorAll('.theme-preset-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const color = e.target.dataset.color;
                this.setPrimaryColor(color);
                document.getElementById('primaryColorInput').value = color;
                document.getElementById('colorLabel').textContent = e.target.textContent;
            });
        });
    }

    // Utility: Convert hex to RGB
    hexToRgb(hex) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        return result ? {
            r: parseInt(result[1], 16),
            g: parseInt(result[2], 16),
            b: parseInt(result[3], 16)
        } : null;
    }

    // Utility: Adjust color brightness
    adjustBrightness(hex, percent) {
        const rgb = this.hexToRgb(hex);
        if (!rgb) return hex;

        const adjust = (value) => {
            return Math.max(0, Math.min(255, value + (value * percent / 100)));
        };

        const r = Math.round(adjust(rgb.r)).toString(16).padStart(2, '0');
        const g = Math.round(adjust(rgb.g)).toString(16).padStart(2, '0');
        const b = Math.round(adjust(rgb.b)).toString(16).padStart(2, '0');

        return `#${r}${g}${b}`;
    }

    // Notify when color changes
    notifyColorChange(color) {
        window.dispatchEvent(new CustomEvent('themeColorChanged', { detail: { color } }));
    }

    // Apply color palette
    applyPalette(paletteKey) {
        const palette = this.colorPalettes[paletteKey];
        if (palette) {
            this.setPrimaryColor(palette.primary);
            document.getElementById('primaryColorInput').value = palette.primary;
            document.getElementById('colorLabel').textContent = palette.name;
        }
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.themeSwitcher = new ThemeSwitcher();
});

// Expose for global access
if (typeof window !== 'undefined') {
    window.ThemeSwitcher = ThemeSwitcher;
}
