/* A progressive view over the already-authorized, paginated provider rows.
   The source list remains available without JavaScript and exposes failures.
   All dates arrive precomputed in the tenant's zone; the browser does no time math. */
(() => {
    const calendar = document.querySelector('[data-task-calendar]');
    if (!calendar) return;
    const grid = calendar.querySelector('[data-calendar-grid]');
    const heading = calendar.querySelector('[data-calendar-month]');
    const entries = [];
    document.querySelectorAll('[data-calendar-provider]').forEach(provider => {
        if (provider.dataset.calendarGridEligible === 'false') return;
        provider.querySelectorAll('[data-calendar-item]').forEach(row => {
            const titleNode = row.querySelector('[data-calendar-link], [data-calendar-title]');
            const date = row.dataset.date;
            if (!titleNode || !/^\d{4}-\d{2}-\d{2}$/.test(date || '')) return;
            entries.push({ date, time: row.dataset.time || '',
                title: titleNode.textContent.trim(), href: titleNode.getAttribute('href'), kind: provider.dataset.calendarProvider });
        });
    });
    const months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
    const monthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
        'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
    const current = calendar.dataset.today;
    const [todayYear, todayMonth] = current.split('-').map(Number);
    let year = Number(calendar.dataset.year);
    let month = Number(calendar.dataset.month) - 1;
    const pad = n => String(n).padStart(2, '0');
    const draw = () => {
        heading.textContent = `${monthNames[month]} ${year}`;
        grid.replaceChildren();
        ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'].forEach(label => {
            const day = document.createElement('div');
            day.className = 'task-calendar-weekday';
            day.setAttribute('aria-hidden', 'true');
            day.textContent = label;
            grid.append(day);
        });
        const first = new Date(Date.UTC(year, month, 1));
        const start = (first.getUTCDay() + 6) % 7;
        const count = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
        const cells = Math.ceil((start + count) / 7) * 7;
        for (let i = 0; i < cells; i++) {
            const date = new Date(Date.UTC(year, month, i - start + 1));
            const iso = `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
            const cell = document.createElement('div');
            cell.className = 'task-calendar-day';
            cell.setAttribute('role', 'listitem');
            const accessibleDate = `${date.getUTCDate()} ${months[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
            cell.setAttribute('aria-label', accessibleDate);
            if (date.getUTCMonth() !== month) cell.classList.add('is-outside');
            if (iso === current) cell.classList.add('is-today');
            const number = document.createElement('time');
            number.dateTime = iso;
            number.setAttribute('aria-hidden', 'true');
            number.textContent = date.getUTCDate();
            cell.append(number);
            entries.filter(entry => entry.date === iso).forEach(entry => {
                const entryNode = entry.href ? document.createElement('a') : document.createElement('span');
                if (entry.href) entryNode.href = entry.href;
                entryNode.className = `task-calendar-entry task-calendar-entry--${entry.kind}`;
                entryNode.textContent = `${entry.time ? entry.time + ' ' : ''}${entry.title}`;
                entryNode.setAttribute('aria-label', `${entry.title}, ${accessibleDate}${entry.time ? ', ' + entry.time : ''}`);
                cell.append(entryNode);
            });
            grid.append(cell);
        }
    };
    const navigate = (targetYear, targetMonth) => {
        if (targetYear < 1900 || targetYear > 2099) return;
        const url = new URL(window.location.href);
        url.search = '';
        url.searchParams.set('year', targetYear);
        url.searchParams.set('month', targetMonth + 1);
        window.location.assign(url);
    };
    const shift = amount => {
        const next = new Date(Date.UTC(year, month + amount, 1));
        navigate(next.getUTCFullYear(), next.getUTCMonth());
    };
    calendar.querySelector('[data-calendar-prev]').addEventListener('click', () => shift(-1));
    calendar.querySelector('[data-calendar-next]').addEventListener('click', () => shift(1));
    calendar.querySelector('[data-calendar-today]').addEventListener('click', () => navigate(todayYear, todayMonth - 1));
    draw();
    calendar.hidden = false;
})();
