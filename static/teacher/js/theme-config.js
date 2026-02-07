/**
 * Theme Configuration
 * Edit this file to customize color palettes and default theme
 */

// Default theme on first visit
const DEFAULT_THEME = 'blue';

// Color Palettes - Add or modify here
const COLOR_PALETTES = {
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
    },

    // ====== ADD YOUR CUSTOM PALETTES BELOW ======
    // Example custom palette:
    // mint: {
    //     primary: '#00d9a3',
    //     secondary: '#00b388',
    //     tertiary: '#00f0c9',
    //     light: '#d1fae5',
    //     lighter: '#ecfdf5',
    //     name: 'Mint'
    // },
};

// Semantic colors (independent of theme)
const SEMANTIC_COLORS = {
    success: '#22c55e',
    warning: '#f59e0b',
    error: '#ef4444',
    info: '#3b82f6'
};

// Animation settings
const ANIMATIONS = {
    colorTransitionDuration: '0.3s', // How fast colors change
    pulseGlowDuration: '2s',         // Pulse animation speed
    borderGlowDuration: '3s'         // Border glow animation speed
};

// Feature flags
const FEATURES = {
    enableColorPicker: true,      // Show custom color picker
    enablePresets: true,          // Show preset buttons
    enableAnimations: true,       // Enable color animations
    enableLogging: false,         // Log color changes to console
    persistPreference: true       // Save color to localStorage
};

// Storage configuration
const STORAGE = {
    keyPrimaryColor: 'iqbal-primary-color',
    keyThemeName: 'iqbal-theme-name',
    keyCustomColors: 'iqbal-custom-colors'
};

// UI Configuration
const UI_CONFIG = {
    pickerSize: 44,              // Color picker input size (px)
    buttonSpacing: 8,            // Gap between preset buttons (px)
    glassBlur: '12px',           // Glass-morphism blur effect
    borderRadius: '12px',        // Button border radius (px)
    showColorLabel: true,        // Show color name next to picker
    compactMode: false           // Reduce UI size on mobile
};

// Export for use in theme-switcher.js
if (typeof window !== 'undefined') {
    window.THEME_CONFIG = {
        DEFAULT_THEME,
        COLOR_PALETTES,
        SEMANTIC_COLORS,
        ANIMATIONS,
        FEATURES,
        STORAGE,
        UI_CONFIG
    };
}

/**
 * HOW TO USE THIS FILE:
 * 
 * 1. CUSTOMIZE PALETTES:
 *    Add new color palette objects with primary, secondary, tertiary colors
 * 
 * 2. CHANGE DEFAULT THEME:
 *    Set DEFAULT_THEME to the palette name you want on first visit
 *    Example: const DEFAULT_THEME = 'purple';
 * 
 * 3. MODIFY ANIMATIONS:
 *    Adjust animation durations for smoother or snappier transitions
 * 
 * 4. TOGGLE FEATURES:
 *    Enable/disable specific features based on your needs
 * 
 * 5. CUSTOMIZE UI:
 *    Adjust picker size, spacing, and other visual elements
 * 
 * EXAMPLE - Add a custom palette:
 * 
 * mint: {
 *     primary: '#00d9a3',      // Main theme color
 *     secondary: '#00b388',    // Darker variant
 *     tertiary: '#00f0c9',     // Lighter variant
 *     light: '#d1fae5',        // Very light
 *     lighter: '#ecfdf5',      // Almost white
 *     name: 'Mint'             // Display name
 * }
 */
