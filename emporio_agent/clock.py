"""Store-local time and opening hours (section 2 of the policy manual)."""

from datetime import date, datetime, time, timedelta, timezone

# Campo Grande/MS is UTC-4 all year (no daylight saving since 2019). A fixed
# offset avoids depending on the IANA tz database, which Windows lacks.
STORE_TZ = timezone(timedelta(hours=-4))

# (open, close) per weekday, Monday=0. Sunday closed.
OPENING_HOURS = {
    0: (time(9), time(18)),
    1: (time(9), time(18)),
    2: (time(9), time(18)),
    3: (time(9), time(18)),
    4: (time(9), time(18)),
    5: (time(9), time(13)),
}

# Fixed-date national holidays (MM-DD). Movable ones (Carnaval, Good Friday,
# Corpus Christi) are not handled — see README limitations.
HOLIDAYS = {"01-01", "04-21", "05-01", "09-07", "10-12", "11-02", "11-15", "11-20", "12-25"}

WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]


def now() -> datetime:
    return datetime.now(STORE_TZ)


def today() -> date:
    return now().date()


def _hours_for(day: date) -> tuple[time, time] | None:
    if day.strftime("%m-%d") in HOLIDAYS:
        return None
    return OPENING_HOURS.get(day.weekday())


def store_status(moment: datetime) -> str:
    """Human-readable open/closed status, in Portuguese, for the system prompt."""
    hours = _hours_for(moment.date())
    if hours and hours[0] <= moment.time() < hours[1]:
        return f"A loja está ABERTA agora (fecha às {hours[1]:%H:%M})."

    reason = "feriado" if moment.strftime("%m-%d") in HOLIDAYS else "fora do horário de funcionamento"
    day = moment.date()
    if hours and moment.time() < hours[0]:
        return f"A loja está FECHADA agora ({reason}). Abre hoje às {hours[0]:%H:%M}."
    for _ in range(14):
        day += timedelta(days=1)
        next_hours = _hours_for(day)
        if next_hours:
            when = "amanhã" if day == moment.date() + timedelta(days=1) else WEEKDAYS_PT[day.weekday()]
            return (f"A loja está FECHADA agora ({reason}). "
                    f"Reabre {when} ({day:%d/%m}) às {next_hours[0]:%H:%M}.")
    return f"A loja está FECHADA agora ({reason})."
