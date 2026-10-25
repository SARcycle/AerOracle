// Additions to the start date picker (dcc.DatePickerSingle 'start-date'), which offers neither by itself:
// - a "Today" button in the open calendar
// - today's date marked in the calendar (class ao-today, see style.css)

(function () {
    const PICKER_ID = 'start-date';
    const MONTHS = [...Array(12).keys()].map((m) => new Date(2000, m, 1).toLocaleString('en', {month: 'long'}));

    function isoDate(date) {
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    }

    function addTodayButton(calendar) {
        if (calendar.querySelector('.ao-today-button')) {
            return;
        }
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'ao-today-button';
        button.textContent = 'Today';
        button.addEventListener('click', (event) => {
            event.preventDefault();
            window.dash_clientside.set_props(PICKER_ID, {date: isoDate(new Date())});
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));  // Close the calendar
        });
        calendar.appendChild(button);
    }

    function markToday(calendar) {
        // The day cells only hold the day number: compare the month and year shown in the calendar's header
        const now = new Date();
        const month = calendar.querySelector('.dash-dropdown-value');
        const year = calendar.querySelector('.dash-input-element');
        const currentMonth = month && year && month.textContent.trim() === MONTHS[now.getMonth()] && Number(year.value) === now.getFullYear();
        calendar.querySelectorAll('.dash-datepicker-calendar td').forEach((cell) => {
            cell.classList.toggle('ao-today', Boolean(currentMonth) && cell.classList.contains('dash-datepicker-calendar-date-inside')
                && cell.textContent.trim() === String(now.getDate()));
        });
    }

    function enhance() {
        document.querySelectorAll('.dash-datepicker-content').forEach((calendar) => {
            addTodayButton(calendar);
            markToday(calendar);
        });
    }

    // The calendar is created when opened and re-rendered on month changes; class changes are not observed (no loop)
    new MutationObserver(enhance).observe(document.body, {childList: true, subtree: true, characterData: true});
    document.addEventListener('input', enhance, true);  // Year typed into the calendar's header
})();
