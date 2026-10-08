/* One theme preference for web and admin: light / dark / system.
 * Resolve before paint, never persist the OS result as an explicit preference. */
(function () {
    'use strict';
    const valid = ['light', 'dark', 'system'];
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    function readPreference() {
        try { const value = localStorage.getItem('theme'); return valid.includes(value) ? value : 'system'; }
        catch (_) { return 'system'; }
    }
    const theme = {
        mode: readPreference(),
        apply() {
            this.isDark = this.mode === 'dark' || (this.mode === 'system' && media.matches);
            const root = document.documentElement;
            root.classList.toggle('dark', this.isDark);
            root.dataset.themePreference = this.mode;
            root.style.colorScheme = this.isDark ? 'dark' : 'light';
            document.querySelectorAll('[data-ui-theme-surface]').forEach(el => {
                el.dataset.theme = this.isDark ? 'dark' : 'light';
            });
            document.querySelectorAll('[data-theme-choice]').forEach(el => {
                el.setAttribute('aria-pressed', String(el.dataset.themeChoice === this.mode));
            });
            document.querySelectorAll('[data-theme-select]').forEach(el => { el.value = this.mode; });
        },
        setMode(mode) {
            if (!valid.includes(mode)) return;
            this.mode = mode;
            try { localStorage.setItem('theme', mode); } catch (_) { /* private browsing */ }
            this.apply();
        }
    };
    window.AppTheme = theme;
    theme.apply();
    document.addEventListener('DOMContentLoaded', () => theme.apply());
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-theme-choice]');
        if (button) theme.setMode(button.dataset.themeChoice);
    });
    document.addEventListener('change', event => {
        if (event.target.matches('[data-theme-select]')) theme.setMode(event.target.value);
    });
    window.addEventListener('storage', event => {
        if (event.key === 'theme' || event.key === null) { theme.mode = readPreference(); theme.apply(); }
    });
    const followSystem = () => { if (theme.mode === 'system') theme.apply(); };
    if (media.addEventListener) media.addEventListener('change', followSystem);
    else media.addListener(followSystem);
})();
