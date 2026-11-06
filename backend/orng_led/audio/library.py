"""Auto composition library. One canvas, no mandatory mirror.

Bar segments are palette on/off, not RGB pixels. Beams are binary. Face is
never painted. invert_segments stays in the renderer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from orng_led.audio.canvas import Canvas, Cell, segment_count
from orng_led.audio.palettes import color_for_fixture
from orng_led.audio.settings import (
    CHARACTER_FAMILIES,
    cast_of,
    colour_of,
    director_home,
    groups_of,
)
from orng_led.config.models import FixtureKind
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw

AUTO_CYCLE_BEATS = (0.5, 1.0, 2.0, 4.0, 8.0)
_COLOR_THEMES = ("orange", "red", "blue", "violet")
_BAR_THEMES = ("orange", "red", "blue", "violet", "bars")
_HEAD_THEMES = ("orange", "red", "blue", "violet", "heads")
_ALL_THEMES = ("orange", "red", "blue", "violet", "bars", "heads")
_STAGE_THEMES = ("orange", "red", "blue", "violet", "heads")


@dataclass(frozen=True)
class Family:
    id: str
    title: str
    motion: str
    envelope: str


@dataclass(frozen=True)
class Composition:
    id: str
    family: str
    title: str
    groups: tuple[str, ...]
    palettes: tuple[str, ...]
    axis: str
    direction: str
    stagger: str
    envelope: str
    bands: int
    tail: float
    polarity: str
    roles: str
    cycles: tuple[float, ...]
    durations: tuple[int, ...]
    transition: str
    pool: str
    needs_segments: bool
    notes: str
    constraints: str
    program: str = "beat"


FAMILIES: tuple[Family, ...] = (
    Family("vertical_wave", "Вертикальна хвиля", "знизу вгору і назад по сегментах", "трикутник"),
    Family(
        "fill_drain", "Заповнення / спустошення", "поріг по осі росте або спадає", "пила / спад"
    ),
    Family("band_tail", "Вузька смуга з хвостом", "біжить пік по полотну", "лінійний спад хвоста"),
    Family("meet", "Зустрічні смуги", "два фронти назустріч", "трикутник зближення"),
    Family("open_close", "Розкриття / збірка", "відкриття від краю або центру", "трикутник"),
    Family("diagonal", "Діагональ по полотну", "u = (x+up)/2", "хвиля або заливка"),
    Family("cascade", "Ступінчастий каскад", "дискретні сходи по барах або висоті", "сходи"),
    Family("multi_band", "Кілька бігучих смуг", "кілька піків на одному циклі", "хвости"),
    Family("spatial_sine", "Просторова синусоїда", "sin по осі або 2d", "синус"),
    Family("layer", "Послідовне наслоєння", "групи входять по третинах циклу", "сходи ролей"),
    Family("negative", "Негативна хвиля", "темне на освітленому тлі", "інверсія поля"),
    Family("dark_window", "Бігуче темне вікно", "щілина в заливці", "вузький провал"),
    Family("phase_pulse", "Пульс зі зсувом", "спільна огинаюча, фаза від координати", "косинус"),
    Family("hit_decay", "Удар і експонента", "різкий пік, плавний спад димера", "exp"),
    Family("double_hit", "Подвійний акцент", "два удари в циклі", "дві експоненти"),
    Family("breathe", "Плавне дихання", "цілий прилад, без малюнка сегментів", "косинус"),
    Family("density_build", "Наростаюча щільність", "більше клітин протягом фрази", "пила"),
    Family("sparse_hits", "Розріджені акценти", "підмножина клітин на удар", "exp"),
    Family("call_response", "Перекличка груп", "дві групи по половинах циклу", "ворота"),
    Family("compound", "Тло + акцент", "повільне поле і швидкий удар", "сума огинаючих"),
)


# Continuous loops, including the seam. A hit, a saw or a shutter is «Під біт».
_SMOOTH_ENVELOPES = frozenset(
    {
        "sine",
        "sine2d",
        "breathe",
        "triangle",
        "window",
        "iris",
        "iris_bottom",
        "oppose",
        "meet",
    }
)


def _c(
    ident: str,
    family: str,
    title: str,
    *,
    groups: tuple[str, ...] = ("bar",),
    palettes: tuple[str, ...] = _BAR_THEMES,
    axis: str = "y",
    direction: str = "up",
    stagger: str = "none",
    envelope: str = "triangle",
    bands: int = 1,
    tail: float = 0.18,
    polarity: str = "positive",
    roles: str = "together",
    cycles: tuple[float, ...] = (1.0, 2.0, 4.0),
    durations: tuple[int, ...] = (8, 16),
    transition: str = "bar",
    pool: str = "phrase",
    needs_segments: bool = True,
    notes: str = "",
    constraints: str = "Сегмент бара — палітра on/off. Діммер бара аналоговий. Голови двійкові, без pan/tilt/strobe.",
    program: str | None = None,
) -> Composition:
    if "bar" not in groups:
        needs_segments = False
        palettes = tuple(name for name in palettes if name != "bars") or _HEAD_THEMES
    if groups == ("bar",):
        palettes = tuple(name for name in palettes if name != "heads") or _BAR_THEMES
    return Composition(
        id=ident,
        family=family,
        title=title,
        groups=groups,
        palettes=palettes,
        axis=axis,
        direction=direction,
        stagger=stagger,
        envelope=envelope,
        bands=max(1, int(bands)),
        tail=float(tail),
        polarity=polarity,
        roles=roles,
        cycles=cycles,
        durations=durations,
        transition=transition,
        pool=pool,
        needs_segments=needs_segments,
        notes=notes,
        constraints=constraints,
        program=program or ("smooth" if envelope in _SMOOTH_ENVELOPES else "beat"),
    )


def _catalog() -> tuple[Composition, ...]:
    all_groups = ("par", "bar", "beam")
    color = _STAGE_THEMES
    return (
        _c(
            "vertical_wave",
            "vertical_wave",
            "Вертикальна хвиля синхронно",
            groups=("bar",),
            notes="Регресійний приклад: усі бари, низ→верх→низ.",
        ),
        _c(
            "vertical_wave_stagger_x",
            "vertical_wave",
            "Вертикальна хвиля зі зсувом по x",
            stagger="x",
        ),
        _c(
            "vertical_wave_stagger_outer",
            "vertical_wave",
            "Крайні бари пізніше",
            stagger="distance",
        ),
        _c(
            "vertical_wave_stagger_inner",
            "vertical_wave",
            "Внутрішні бари пізніше",
            stagger="inner",
        ),
        _c("vertical_wave_down", "vertical_wave", "Хвиля згори вниз і назад", direction="down"),
        _c(
            "vertical_wave_scan_bars",
            "vertical_wave",
            "Хвиля по барах як по сходах",
            axis="bar",
            needs_segments=False,
            notes="Кожен бар — сходинка полотна.",
        ),
        _c(
            "vertical_wave_with_pars",
            "vertical_wave",
            "Хвиля барів, PAR дихають по висоті",
            groups=all_groups,
            palettes=color,
            roles="pars_echo",
        ),
        _c("fill_up", "fill_drain", "Заповнення знизу", envelope="saw"),
        _c("fill_down", "fill_drain", "Заповнення згори", direction="down", envelope="saw"),
        _c("drain_down", "fill_drain", "Спустошення вниз", envelope="fall"),
        _c("fill_left", "fill_drain", "Заповнення зліва направо", axis="x", direction="right", envelope="saw"),
        _c(
            "fill_right",
            "fill_drain",
            "Заповнення справа наліво",
            axis="x",
            direction="left",
            envelope="saw",
        ),
        _c(
            "fill_center_x",
            "fill_drain",
            "Заповнення від осі",
            axis="x",
            direction="out",
            envelope="saw",
        ),
        _c(
            "drain_center_x",
            "fill_drain",
            "Спустошення до осі",
            axis="x",
            direction="in",
            envelope="fall",
        ),
        _c("band_up", "band_tail", "Смуга вгору з хвостом", envelope="travel"),
        _c("band_down", "band_tail", "Смуга вниз з хвостом", direction="down", envelope="travel"),
        _c("band_left", "band_tail", "Смуга вліво", axis="x", direction="left", envelope="travel"),
        _c("band_right", "band_tail", "Смуга вправо", axis="x", direction="right", envelope="travel"),
        _c("band_diag", "band_tail", "Діагональна смуга", axis="diag", envelope="travel"),
        _c(
            "band_diag_flip",
            "band_tail",
            "Зворотна діагональна смуга",
            axis="diag",
            direction="down",
            envelope="travel",
        ),
        _c("band_up_long_tail", "band_tail", "Широкий хвіст вгору", envelope="travel", tail=0.34),
        _c(
            "band_left_short",
            "band_tail",
            "Коротка смуга вліво",
            axis="x",
            direction="left",
            envelope="travel",
            tail=0.10,
            cycles=(0.5, 1.0, 2.0),
        ),
        _c("meet_vertical", "meet", "Зустріч зверху і знизу", envelope="meet"),
        _c("meet_horizontal", "meet", "Зустріч зліва і справа", axis="x", envelope="meet"),
        _c("meet_diag", "meet", "Зустріч по діагоналі", axis="diag", envelope="meet"),
        _c("meet_bounce", "meet", "Зустріч і розбіжність", envelope="oppose", tail=0.16),
        _c(
            "bloom",
            "open_close",
            "Розкриття від центру ряду",
            axis="x",
            direction="out",
            stagger="distance",
            notes="Колишній bloom: внутрішні бари раніше.",
        ),
        _c("open_bottom", "open_close", "Розкриття від низу полотна", envelope="iris_bottom"),
        _c(
            "open_center_y",
            "open_close",
            "Розкриття від середини висоти",
            direction="out",
            envelope="iris",
        ),
        _c(
            "open_center_x",
            "open_close",
            "Розкриття від осі x",
            axis="x",
            direction="out",
            envelope="iris",
        ),
        _c("open_outer", "open_close", "Збірка з країв", axis="x", direction="in", envelope="iris"),
        _c("iris", "open_close", "Ірис полотна", axis="radial", envelope="iris"),
        _c("diag_wave", "diagonal", "Діагональна хвиля", axis="diag", envelope="triangle"),
        _c(
            "diag_wave_rev",
            "diagonal",
            "Діагональна хвиля назад",
            axis="diag",
            direction="down",
            envelope="triangle",
        ),
        _c("diag_fill", "diagonal", "Діагональне заповнення", axis="diag", envelope="saw"),
        _c(
            "diag_fill_rev",
            "diagonal",
            "Діагональне заповнення назад",
            axis="diag",
            direction="down",
            envelope="fall",
        ),
        _c("diag_cascade", "diagonal", "Діагональний каскад", axis="diag", envelope="cascade"),
        _c("cascade_left", "cascade", "Каскад зліва", axis="bar", envelope="cascade"),
        _c(
            "cascade_right",
            "cascade",
            "Каскад справа",
            axis="bar",
            direction="left",
            envelope="cascade",
        ),
        _c("cascade_up", "cascade", "Каскад вгору по висоті", envelope="cascade"),
        _c("cascade_down", "cascade", "Каскад вниз", direction="down", envelope="cascade"),
        _c(
            "cascade_bars_then_segments",
            "cascade",
            "Спочатку бари, потім сегменти",
            axis="bar",
            envelope="cascade",
            stagger="y",
        ),
        _c("bands_2_up", "multi_band", "Дві смуги вгору", bands=2, envelope="travel"),
        _c("bands_3_up", "multi_band", "Три смуги вгору", bands=3, envelope="travel", tail=0.12),
        _c(
            "bands_2_left",
            "multi_band",
            "Дві смуги вліво",
            axis="x",
            direction="left",
            bands=2,
            envelope="travel",
        ),
        _c(
            "bands_2_diag",
            "multi_band",
            "Дві діагональні смуги",
            axis="diag",
            bands=2,
            envelope="travel",
        ),
        _c("bands_oppose", "multi_band", "Дві зустрічні смуги", bands=2, envelope="oppose"),
        _c("sine_vertical", "spatial_sine", "Стояча синусоїда по висоті", envelope="sine"),
        _c("sine_horizontal", "spatial_sine", "Синусоїда по ряду", axis="x", envelope="sine"),
        _c("sine_2d", "spatial_sine", "Синусоїда x+up", axis="diag", envelope="sine2d"),
        _c("sine_travel", "spatial_sine", "Біжуча синусоїда", envelope="sine_travel"),
        _c(
            "sine_standing", "spatial_sine", "Стояча хвиля з диханням", envelope="sine", stagger="y"
        ),
        _c(
            "layer_bars_pars",
            "layer",
            "Спочатку бари, потім PAR",
            groups=all_groups,
            palettes=color,
            roles="layer_bars_pars",
            needs_segments=True,
        ),
        _c(
            "layer_pars_bars",
            "layer",
            "Спочатку PAR, потім бари",
            groups=all_groups,
            palettes=color,
            roles="layer_pars_bars",
        ),
        _c(
            "layer_left_right",
            "layer",
            "Ліва половина, потім права",
            groups=all_groups,
            palettes=color,
            axis="x",
            roles="layer_left_right",
        ),
        _c(
            "layer_bottom_top",
            "layer",
            "Низ, потім верх",
            groups=("bar",),
            roles="layer_bottom_top",
        ),
        _c(
            "layer_corners",
            "layer",
            "Кути полотна по черзі",
            groups=all_groups,
            palettes=color,
            axis="radial",
            roles="layer_corners",
        ),
        _c(
            "negative_wave_up",
            "negative",
            "Темна хвиля вгору",
            polarity="negative",
            envelope="travel",
            tail=0.22,
        ),
        _c(
            "negative_wave_left",
            "negative",
            "Темна хвиля вліво",
            axis="x",
            direction="left",
            polarity="negative",
            envelope="travel",
        ),
        _c(
            "negative_diag",
            "negative",
            "Темна діагональ",
            axis="diag",
            polarity="negative",
            envelope="travel",
        ),
        _c(
            "negative_pulse_hole",
            "negative",
            "Пульсуюча дірка",
            polarity="negative",
            envelope="iris",
            axis="radial",
        ),
        _c("window_up", "dark_window", "Темне вікно вгору", envelope="window"),
        _c("window_left", "dark_window", "Темне вікно вліво", axis="x", direction="left", envelope="window"),
        _c("window_two", "dark_window", "Два темні вікна", bands=2, envelope="window"),
        _c("shutter", "dark_window", "Шторка що сходиться", axis="y", envelope="shutter"),
        _c(
            "pulse",
            "phase_pulse",
            "Спільний пульс",
            groups=all_groups,
            palettes=color,
            needs_segments=False,
            envelope="pulse",
            cycles=(0.5, 1.0, 2.0),
            durations=(8,),
            transition="beat",
        ),
        _c(
            "pulse_by_x",
            "phase_pulse",
            "Пульс зі зсувом по x",
            groups=all_groups,
            palettes=color,
            axis="x",
            stagger="x",
            needs_segments=False,
            envelope="pulse",
        ),
        _c(
            "pulse_by_y",
            "phase_pulse",
            "Пульс зі зсувом по висоті",
            groups=("bar",),
            stagger="y",
            envelope="pulse_scan",
        ),
        _c(
            "pulse_by_bar",
            "phase_pulse",
            "Пульс по індексу бара",
            axis="bar",
            stagger="bar",
            envelope="pulse",
            needs_segments=False,
        ),
        _c(
            "pulse_pars_lag",
            "phase_pulse",
            "PAR відстають від барів",
            groups=all_groups,
            palettes=color,
            roles="pars_echo",
            envelope="pulse",
            needs_segments=False,
        ),
        _c(
            "accent",
            "hit_decay",
            "Удар і експонента на всіх",
            groups=all_groups,
            palettes=_ALL_THEMES,
            needs_segments=False,
            envelope="hit",
            cycles=(1.0, 2.0),
            durations=(2, 4),
            transition="beat",
            pool="accent",
        ),
        _c(
            "hit_bars",
            "hit_decay",
            "Удар тільки барів",
            envelope="hit",
            needs_segments=False,
            cycles=(1.0,),
            durations=(2, 4),
            transition="beat",
            pool="accent",
        ),
        _c(
            "hit_pars",
            "hit_decay",
            "Удар PAR",
            groups=("par",),
            palettes=_HEAD_THEMES,
            envelope="hit",
            needs_segments=False,
            cycles=(1.0,),
            durations=(2,),
            transition="beat",
            pool="accent",
        ),
        _c(
            "hit_bars_pars_decay",
            "hit_decay",
            "Бари б'ють, PAR гаснуть довше",
            groups=("par", "bar"),
            palettes=color,
            roles="pars_echo",
            envelope="hit",
            needs_segments=False,
            pool="accent",
            durations=(4,),
        ),
        _c(
            "hit_wide",
            "hit_decay",
            "Широкий спад",
            groups=all_groups,
            palettes=color,
            envelope="hit_wide",
            needs_segments=False,
            pool="accent",
            durations=(4,),
        ),
        _c(
            "double_hit",
            "double_hit",
            "Два удари",
            groups=all_groups,
            palettes=color,
            envelope="double",
            needs_segments=False,
            cycles=(1.0, 2.0),
            durations=(4, 8),
            pool="accent",
        ),
        _c(
            "double_hit_roles",
            "double_hit",
            "Удар барів, потім PAR",
            groups=("par", "bar"),
            palettes=color,
            roles="bars_lead",
            envelope="double",
            needs_segments=False,
            pool="accent",
        ),
        _c(
            "double_hit_offbeat",
            "double_hit",
            "Доля і слабка доля",
            groups=all_groups,
            palettes=color,
            envelope="double_offbeat",
            needs_segments=False,
            pool="accent",
        ),
        _c(
            "wash",
            "breathe",
            "Спокійна заливка",
            groups=all_groups,
            palettes=_ALL_THEMES,
            needs_segments=False,
            envelope="breathe",
            cycles=(4.0, 8.0),
            durations=(16, 32),
            pool="calm",
        ),
        _c(
            "breathe_bars",
            "breathe",
            "Дихання барів",
            needs_segments=False,
            envelope="breathe",
            pool="calm",
            durations=(16,),
        ),
        _c(
            "breathe_pars",
            "breathe",
            "Дихання PAR",
            groups=("par",),
            palettes=_HEAD_THEMES,
            needs_segments=False,
            envelope="breathe",
            pool="calm",
        ),
        _c(
            "breathe_mirror",
            "breathe",
            "Симетричне дихання від осі",
            groups=all_groups,
            palettes=color,
            stagger="distance",
            needs_segments=False,
            envelope="breathe",
            notes="Симетрія як художній варіант, не закон.",
            pool="calm",
        ),
        _c(
            "breathe_opposite",
            "breathe",
            "Ліва і права у протифазі",
            groups=all_groups,
            palettes=color,
            stagger="opposite",
            needs_segments=False,
            envelope="breathe",
            pool="calm",
        ),
        _c(
            "breathe_gradient",
            "breathe",
            "Дихання з градієнтом висоти",
            envelope="breathe",
            stagger="y",
            pool="calm",
        ),
        _c(
            "density",
            "density_build",
            "Набір приладів від осі",
            groups=all_groups,
            palettes=color,
            axis="x",
            direction="out",
            envelope="saw",
            needs_segments=False,
            pool="rise",
        ),
        _c(
            "density_segments",
            "density_build",
            "Набір сегментів знизу",
            envelope="density",
            pool="rise",
        ),
        _c(
            "density_groups",
            "density_build",
            "PAR, бари, потім голови",
            groups=all_groups,
            palettes=color,
            roles="layer_bars_pars",
            envelope="saw",
            needs_segments=False,
            pool="rise",
        ),
        _c(
            "density_beams_last",
            "density_build",
            "Голови входять останніми",
            groups=all_groups,
            palettes=color,
            roles="beams_last",
            envelope="saw",
            needs_segments=False,
            pool="rise",
        ),
        _c(
            "sparse_alternate",
            "sparse_hits",
            "Через бар",
            axis="bar",
            envelope="hit",
            stagger="alternate",
        ),
        _c(
            "sparse_corners",
            "sparse_hits",
            "Кути полотна",
            axis="radial",
            envelope="hit",
            roles="corners",
        ),
        _c(
            "sparse_inner",
            "sparse_hits",
            "Тільки внутрішні бари",
            axis="x",
            envelope="hit",
            roles="inner",
        ),
        _c(
            "sparse_every_third",
            "sparse_hits",
            "Кожна третя клітина",
            envelope="hit",
            roles="every_third",
        ),
        _c(
            "groups",
            "call_response",
            "Перекличка ліво/право",
            groups=all_groups,
            palettes=color,
            axis="x",
            roles="call_lr",
            envelope="pulse",
            needs_segments=False,
            cycles=(1.0, 2.0, 4.0),
        ),
        _c(
            "call_bars_pars",
            "call_response",
            "Перекличка бари/PAR",
            groups=("par", "bar"),
            palettes=color,
            roles="call_bars_pars",
            envelope="pulse",
            needs_segments=False,
        ),
        _c(
            "call_inner_outer",
            "call_response",
            "Внутрішні / крайні",
            groups=("bar",),
            axis="x",
            roles="call_inner_outer",
        ),
        _c(
            "call_beams",
            "call_response",
            "Голови відповідають барам",
            groups=all_groups,
            palettes=color,
            roles="call_beams",
            envelope="pulse",
            needs_segments=False,
        ),
        _c(
            "compound_breathe_hit",
            "compound",
            "Дихання тла і удар",
            groups=all_groups,
            palettes=color,
            envelope="compound_breathe_hit",
            needs_segments=False,
            cycles=(2.0, 4.0),
        ),
        _c(
            "compound_fill_band",
            "compound",
            "Повільне заповнення і швидка смуга",
            envelope="compound_fill_band",
            cycles=(2.0, 4.0, 8.0),
        ),
        _c(
            "compound_sine_cascade",
            "compound",
            "Синус тла і каскад",
            envelope="compound_sine_cascade",
        ),
        _c(
            "compound_negative_window",
            "compound",
            "Негатив і бігуче вікно",
            envelope="compound_negative_window",
        ),
        _c(
            "compound_wave_accent",
            "compound",
            "Вертикальна хвиля і удар димера",
            envelope="compound_wave_accent",
            cycles=(1.0, 2.0, 4.0),
        ),
        _c(
            "breathe_heads",
            "breathe",
            "Дихання PAR і голів",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            needs_segments=False,
            envelope="breathe",
            cycles=(4.0, 8.0),
            durations=(16, 32),
        ),
        _c(
            "breathe_heads_split",
            "breathe",
            "PAR і голови в протифазі",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            stagger="opposite",
            needs_segments=False,
            envelope="breathe",
            cycles=(4.0, 8.0),
            durations=(16, 32),
        ),
        _c(
            "sine_heads",
            "spatial_sine",
            "Синус по ряду голів",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            axis="x",
            needs_segments=False,
            envelope="sine",
            cycles=(4.0, 8.0),
        ),
        _c(
            "sine_heads_travel",
            "spatial_sine",
            "Біжучий синус голів",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            axis="x",
            needs_segments=False,
            envelope="sine_travel",
            cycles=(2.0, 4.0, 8.0),
        ),
        _c(
            "layer_heads_lr",
            "layer",
            "Ліва і права голови по черзі",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            axis="x",
            roles="layer_left_right",
            needs_segments=False,
            envelope="breathe",
        ),
        _c(
            "hit_heads",
            "hit_decay",
            "Удар PAR і голів",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            needs_segments=False,
            envelope="hit",
            cycles=(1.0, 2.0),
            durations=(8, 16),
            transition="beat",
        ),
        _c(
            "hit_heads_wide",
            "hit_decay",
            "Широкий удар голів",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            needs_segments=False,
            envelope="hit_wide",
            cycles=(1.0, 2.0),
            durations=(8, 16),
            transition="beat",
        ),
        _c(
            "hit_heads_stagger",
            "hit_decay",
            "Удар голів зі зсувом по x",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            axis="x",
            stagger="x",
            needs_segments=False,
            envelope="hit",
            cycles=(1.0, 2.0),
            durations=(8, 16),
            transition="beat",
        ),
        _c(
            "double_hit_heads",
            "double_hit",
            "Два удари голів",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            needs_segments=False,
            envelope="double",
            cycles=(1.0, 2.0),
            durations=(8, 16),
        ),
        _c(
            "sparse_heads",
            "sparse_hits",
            "Розріджений удар голів",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            axis="x",
            stagger="alternate",
            needs_segments=False,
            envelope="hit",
            cycles=(1.0, 2.0),
            durations=(8, 16),
            transition="beat",
        ),
        _c(
            "cascade_heads",
            "cascade",
            "Каскад голів по ряду",
            groups=("par", "beam"),
            palettes=_HEAD_THEMES,
            axis="x",
            needs_segments=False,
            envelope="cascade",
            cycles=(1.0, 2.0, 4.0),
        ),
        _c(
            "heads_only_fan",
            "open_close",
            "Тільки голови: віяло",
            groups=("beam",),
            axis="x",
            direction="out",
            envelope="breathe",
            roles="fan",
            cycles=(8.0, 16.0, 32.0),
            durations=(16, 32),
            program="smooth",
            notes="PAR і бари вимкнені. Повільне розкриття голів.",
        ),
        _c(
            "heads_only_arcs",
            "meet",
            "Тільки голови: дуги",
            groups=("beam",),
            axis="y",
            direction="up",
            envelope="breathe",
            roles="arcs",
            cycles=(8.0, 16.0, 32.0),
            durations=(16, 32),
            program="smooth",
            notes="PAR і бари вимкнені. Дзеркальні дуги.",
        ),
        _c(
            "heads_only_rise",
            "vertical_wave",
            "Тільки голови: підйом",
            groups=("beam",),
            axis="y",
            direction="up",
            envelope="breathe",
            roles="rise",
            cycles=(8.0, 16.0, 32.0),
            durations=(16, 32),
            program="smooth",
            notes="PAR і бари вимкнені. Підйом знизу, pan у центрі калібрування.",
        ),
        _c(
            "heads_only_oval",
            "spatial_sine",
            "Тільки голови: овали",
            groups=("beam",),
            axis="x",
            envelope="breathe",
            roles="oval",
            cycles=(8.0, 16.0, 32.0),
            durations=(16, 32),
            program="smooth",
            notes="PAR і бари вимкнені. Невеликі дзеркальні овали.",
        ),
        _c(
            "heads_only_meet",
            "open_close",
            "Тільки голови: зведення",
            groups=("beam",),
            axis="x",
            direction="in",
            envelope="breathe",
            roles="converge",
            cycles=(8.0, 16.0, 32.0),
            durations=(16, 32),
            program="smooth",
            notes="PAR і бари вимкнені. Плавне зведення і розведення.",
        ),
        _c(
            "heads_beat_pass",
            "hit_decay",
            "Тільки голови: прохід на долі",
            groups=("beam",),
            axis="y",
            direction="up",
            envelope="hit",
            roles="pass",
            cycles=(2.0, 4.0, 8.0),
            durations=(8, 16),
            program="beat",
            notes="PAR і бари вимкнені. Механіка на 2/4/8 долях, без апаратного strobe.",
        ),
        _c(
            "heads_rise_pulse",
            "breathe",
            "Тільки голови: підйом із серією",
            groups=("beam",),
            axis="y",
            direction="up",
            envelope="breathe",
            roles="head_pulse",
            cycles=(4.0, 8.0),
            durations=(8, 16),
            program="beat",
            notes="Серія димера лише зі свіжою сіткою і підтвердженим розгоном. Інакше рівний підйом. Апаратний strobe не чіпається.",
        ),
    )


_LEGACY_LOOKS = ("pulse", "groups", "wash", "bloom", "accent", "density")


def _ordered(items: tuple[Composition, ...]) -> tuple[Composition, ...]:
    by_id = {item.id: item for item in items}
    head = [by_id[name] for name in _LEGACY_LOOKS if name in by_id]
    rest = [item for item in items if item.id not in _LEGACY_LOOKS]
    return tuple(head + rest)


COMPOSITIONS: tuple[Composition, ...] = _ordered(_catalog())
BY_ID: dict[str, Composition] = {item.id: item for item in COMPOSITIONS}
LOOKS: tuple[str, ...] = tuple(item.id for item in COMPOSITIONS)
REGRESSION_VERTICAL_WAVE = "vertical_wave"


def look_name(pattern: int) -> str:
    return LOOKS[int(pattern) % len(LOOKS)]


def composition_by_id(ident: str | None) -> Composition | None:
    if ident is None:
        return None
    return BY_ID.get(str(ident))


def compositions_for(
    pool: str,
    theme: str,
    *,
    character: str = "rhythmic",
    cast: str | None = None,
    fixtures: list[object] | None = None,
) -> tuple[Composition, ...]:
    """Catalogue for a pool. Colour does not filter who is on stage."""
    chosen_cast = cast_of(theme, cast)
    theme_groups = groups_of(chosen_cast)
    families = CHARACTER_FAMILIES.get(character, CHARACTER_FAMILIES["rhythmic"])
    found = []
    for item in COMPOSITIONS:
        if item.pool != pool:
            continue
        if pool == "calm" and item.family not in families and item.id not in {"wash", "breathe_pars"}:
            continue
        # Accent pool is a short answer, not a second character catalogue.
        if not set(item.groups).intersection(theme_groups):
            continue
        if item.needs_segments and "bar" not in theme_groups:
            continue
        if fixtures is not None and not composition_fits(item, fixtures, theme):
            continue
        found.append(item)
    return tuple(found)


# Same-direction relatives. Not a script every track must follow.
_FAMILY_NEXT: dict[str, tuple[str, ...]] = {
    "vertical_wave": ("fill_drain", "cascade", "multi_band", "density_build"),
    "fill_drain": ("vertical_wave", "open_close", "cascade"),
    "cascade": ("vertical_wave", "band_tail", "multi_band"),
    "band_tail": ("cascade", "multi_band", "meet"),
    "multi_band": ("vertical_wave", "band_tail", "density_build"),
    "meet": ("band_tail", "open_close", "diagonal"),
    "open_close": ("fill_drain", "spatial_sine", "vertical_wave"),
    "spatial_sine": ("vertical_wave", "breathe", "open_close"),
    "breathe": ("spatial_sine", "compound"),
    "compound": ("layer", "breathe", "vertical_wave"),
    "layer": ("compound", "call_response"),
    "call_response": ("layer", "phase_pulse"),
    "phase_pulse": ("call_response", "cascade"),
    "diagonal": ("cascade", "meet"),
    "density_build": ("vertical_wave", "multi_band", "hit_decay"),
    "hit_decay": ("double_hit", "sparse_hits", "cascade"),
    "double_hit": ("hit_decay", "sparse_hits"),
    "sparse_hits": ("hit_decay", "dark_window"),
    "dark_window": ("negative", "sparse_hits"),
    "negative": ("dark_window", "hit_decay"),
}


def bed_looks() -> tuple[str, ...]:
    """Background layers. The director does not treat these as the main motif."""
    return tuple(item.id for item in COMPOSITIONS if item.id in {"breathe_pars", "breathe_bars"})


def gallery_looks() -> tuple[str, ...]:
    """Every composed look the gallery can paint. Not a playlist."""
    return LOOKS


def director_looks(
    pool: str,
    theme: str,
    *,
    character: str = "rhythmic",
    current: str | None = None,
    fixtures: list[object] | None = None,
    limit: int | None = None,
) -> tuple[str, ...]:
    """Motif candidates for this pool. Character orders the list; it does not hide the catalogue."""
    catalog = compositions_for(
        pool, theme, character=character, fixtures=fixtures
    )
    if not catalog:
        return ()
    by_id = {item.id: item for item in catalog}
    ordered: list[str] = []

    def _push(name: str | None) -> None:
        if name and name in by_id and name not in ordered:
            ordered.append(name)

    home = director_home(character, theme)
    _push(current)
    _push(home)
    current_item = BY_ID.get(str(current or ""))
    if current_item is not None:
        for item in catalog:
            if item.family == current_item.family and item.direction == current_item.direction:
                _push(item.id)
        for family in _FAMILY_NEXT.get(current_item.family, ()):
            for item in catalog:
                if item.family == family:
                    _push(item.id)
                    break
    families = CHARACTER_FAMILIES.get(character, CHARACTER_FAMILIES["rhythmic"])
    for item in catalog:
        if item.family in families:
            _push(item.id)
    for item in catalog:
        _push(item.id)
        if limit is not None and len(ordered) >= limit:
            break
    return tuple(ordered if limit is None else ordered[:limit])


def usage_map() -> dict[str, tuple[str, ...]]:
    """Where each look is allowed: gallery, bed, or a director neighbourhood."""
    director: list[str] = []
    for character in CHARACTER_FAMILIES:
        for theme in ("orange", "bars", "heads"):
            home = director_home(character, theme)
            for pool in ("phrase", "calm", "rise", "accent"):
                director.extend(
                    director_looks(pool, theme, character=character, current=home)
                )
    return {
        "gallery": gallery_looks(),
        "bed": bed_looks(),
        "director": tuple(dict.fromkeys(director)),
    }


def composition_fits(item: Composition, fixtures: list[object], palette: str) -> bool:
    colour = colour_of(palette, palette)
    del colour
    present = {getattr(fixture, "kind", None) for fixture in fixtures}
    wanted = {_kind(name) for name in item.groups}
    if not wanted.intersection(present):
        return False
    if item.needs_segments:
        bars = [
            fixture for fixture in fixtures if getattr(fixture, "kind", None) is FixtureKind.BAR
        ]
        if not any(segment_count(fixture) >= 4 for fixture in bars):
            return False
    return True


def resolve_composition(
    look: str | None,
    pattern: int,
    palette: str,
    fixtures: list[object],
) -> Composition:
    chosen = composition_by_id(look) if look else None
    explicit = chosen is not None
    if chosen is None:
        chosen = BY_ID[look_name(pattern)]
    if composition_fits(chosen, fixtures, palette):
        return chosen
    # An operator or planner look must not expand into wash/all fixtures.
    if explicit:
        return chosen
    fallback = BY_ID["wash"] if "wash" in BY_ID else COMPOSITIONS[0]
    if composition_fits(fallback, fixtures, palette):
        return fallback
    for item in COMPOSITIONS:
        if composition_fits(item, fixtures, palette):
            return item
    return chosen


def _kind(name: str) -> FixtureKind | None:
    return {"par": FixtureKind.PAR, "bar": FixtureKind.BAR, "beam": FixtureKind.BEAM}.get(name)


def _triangle(phase: float) -> float:
    return 1.0 - abs(2.0 * (phase % 1.0) - 1.0)


def _saw(phase: float) -> float:
    return phase % 1.0


def _fall(phase: float) -> float:
    return 1.0 - (phase % 1.0)


def _hit(phase: float, tau: float) -> float:
    return math.exp(-(phase % 1.0) / max(0.04, tau))


def _breathe(phase: float) -> float:
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * (phase % 1.0))


def _mirror_x(cell: Cell) -> float:
    return abs(cell.x - 0.5) * 2.0


def _axis_u(cell: Cell, axis: str, direction: str, bar_count: int) -> float:
    # Bars are one canvas. Only PAR and head pairs fold onto the mirror axis,
    # and only when the look itself is not an explicit left/right pass.
    across = direction in {"left", "right", "reverse"}
    pair = cell.kind in {FixtureKind.PAR, FixtureKind.BEAM} and not across
    mirrored = False
    if axis == "x":
        if pair:
            u = _mirror_x(cell)
            mirrored = True
        else:
            u = cell.x
    elif axis == "bar":
        u = 0.5 if bar_count <= 1 or cell.bar_index < 0 else cell.bar_index / (bar_count - 1)
    elif axis == "diag":
        if pair:
            u = 0.5 * (_mirror_x(cell) + cell.up)
            mirrored = True
        else:
            u = 0.5 * (cell.x + cell.up)
    elif axis == "radial":
        u = min(1.0, math.hypot(cell.x - 0.5, cell.up - 0.5) / 0.72)
        mirrored = True
    else:
        u = cell.up
    if direction in {"down", "left", "reverse"}:
        u = 1.0 - u
    if direction == "out":
        if not mirrored:
            u = abs(u - 0.5) * 2.0
    if direction == "in":
        if mirrored:
            u = 1.0 - u
        else:
            u = 1.0 - abs(u - 0.5) * 2.0
    return max(0.0, min(1.0, u))


def _stagger(cell: Cell, name: str, bar_count: int) -> float:
    if name == "x":
        if cell.kind in {FixtureKind.PAR, FixtureKind.BEAM}:
            return 0.28 * abs(cell.x - 0.5) * 2.0
        return 0.28 * cell.x
    if name == "y":
        return 0.28 * cell.up
    if name == "bar":
        if bar_count <= 1 or cell.bar_index < 0:
            return 0.0
        return 0.22 * cell.bar_index / (bar_count - 1)
    if name == "distance":
        return 0.45 * abs(cell.x - 0.5) * 2.0
    if name == "inner":
        return 0.45 * (1.0 - abs(cell.x - 0.5) * 2.0)
    if name == "alternate":
        return 0.5 if cell.bar_index % 2 else 0.0
    if name == "opposite":
        return 0.0 if cell.x < 0.5 else 0.5
    if name == "mirror":
        return 0.0
    return 0.0


def _role_gate(cell: Cell, roles: str, phase: float) -> float:
    left = cell.x < 0.5
    inner = abs(cell.x - 0.5) < 0.12
    if roles == "together":
        return 1.0
    if roles == "pars_echo" and cell.kind is FixtureKind.PAR:
        return 1.0
    if roles == "bars_lead" and cell.kind is FixtureKind.PAR:
        return 1.0
    if roles == "call_lr":
        return 1.0 if (phase % 1.0 < 0.5) == left else 0.0
    if roles == "call_bars_pars":
        bars = phase % 1.0 < 0.5
        if bars:
            return 1.0 if cell.kind is FixtureKind.BAR else 0.15
        return 1.0 if cell.kind is FixtureKind.PAR else 0.15
    if roles == "call_inner_outer":
        want_inner = phase % 1.0 < 0.5
        return 1.0 if want_inner == inner else 0.0
    if roles == "call_beams":
        if phase % 1.0 < 0.55:
            return 1.0 if cell.kind is not FixtureKind.BEAM else 0.0
        return 1.0 if cell.kind is FixtureKind.BEAM else 0.2
    if roles == "corners":
        corner = (cell.x < 0.4 or cell.x > 0.6) and (cell.up < 0.35 or cell.up > 0.65)
        return 1.0 if corner else 0.0
    if roles == "inner":
        return 1.0 if inner else 0.0
    if roles == "every_third":
        return 1.0 if (cell.bar_index + cell.segment) % 3 == 0 else 0.0
    if roles == "layer_bars_pars":
        if phase < 0.38:
            return 1.0 if cell.kind is FixtureKind.BAR else 0.0
        if phase < 0.72:
            return 1.0 if cell.kind in {FixtureKind.BAR, FixtureKind.PAR} else 0.0
        return 1.0
    if roles == "layer_pars_bars":
        if phase < 0.38:
            return 1.0 if cell.kind is FixtureKind.PAR else 0.0
        if phase < 0.72:
            return 1.0 if cell.kind in {FixtureKind.BAR, FixtureKind.PAR} else 0.0
        return 1.0
    if roles == "layer_left_right":
        if phase < 0.5:
            return 1.0 if left else 0.0
        return 1.0
    if roles == "layer_bottom_top":
        if phase < 0.5:
            return 1.0 if cell.up < 0.5 else 0.0
        return 1.0
    if roles == "layer_corners":
        step = int((phase % 1.0) * 4.0)
        quad = (0 if cell.x < 0.5 else 1) + (0 if cell.up < 0.5 else 2)
        return 1.0 if quad <= step else 0.0
    if roles == "beams_last":
        if phase < 0.7:
            return 0.0 if cell.kind is FixtureKind.BEAM else 1.0
        return 1.0
    return 1.0


def _field(item: Composition, cell: Cell, phase: float, bar_count: int) -> float:
    local = (phase - _stagger(cell, item.stagger, bar_count)) % 1.0
    if item.roles == "pars_echo" and cell.kind is FixtureKind.PAR:
        local = (local - 0.22) % 1.0
    if item.roles == "bars_lead" and cell.kind is FixtureKind.PAR:
        local = (local - 0.5) % 1.0
    u = _axis_u(cell, item.axis, item.direction, bar_count)
    env = item.envelope
    bands = max(1, item.bands)
    tail = max(0.06, item.tail)
    if env == "triangle":
        height = _triangle(local)
        value = 1.0 if u <= max(height, 0.08) + 0.02 else 0.0
    elif env == "saw":
        value = 1.0 if u <= _saw(local) + 0.02 else 0.0
    elif env == "fall":
        value = 1.0 if u <= _fall(local) + 0.02 else 0.0
    elif env == "travel":
        value = 0.0
        for index in range(bands):
            pos = (local + index / bands) % 1.0
            value = max(value, max(0.0, 1.0 - abs(u - pos) / tail))
    elif env == "oppose":
        a = max(0.0, 1.0 - abs(u - local) / tail)
        b = max(0.0, 1.0 - abs(u - (1.0 - local)) / tail)
        value = max(a, b)
    elif env == "meet":
        edge = 0.5 * _triangle(local)
        value = 1.0 if u <= edge or u >= 1.0 - edge else 0.0
    elif env == "iris":
        radius = _triangle(local)
        value = 1.0 if u <= radius + 0.03 else 0.0
    elif env == "iris_bottom":
        radius = _triangle(local)
        dist = min(1.0, math.hypot(cell.x - 0.5, cell.up) / 0.85)
        value = 1.0 if dist <= radius + 0.03 else 0.0
    elif env == "cascade":
        steps = max(4, bar_count if item.axis == "bar" else 8)
        level = int(_saw(local) * steps + 1e-6)
        index = int(u * (steps - 1) + 1e-6)
        value = 1.0 if index <= level else 0.0
    elif env == "sine":
        value = 0.5 + 0.5 * math.sin(2.0 * math.pi * (u - 0.15 * _breathe(local)))
    elif env == "sine2d":
        span = _mirror_x(cell) if cell.kind in {FixtureKind.PAR, FixtureKind.BEAM} else cell.x
        value = 0.5 + 0.5 * math.sin(2.0 * math.pi * (span + cell.up - local))
    elif env == "sine_travel":
        value = 0.5 + 0.5 * math.sin(2.0 * math.pi * (u - local))
    elif env == "window":
        value = 1.0
        for index in range(bands):
            pos = (local + index / bands) % 1.0
            if abs(u - pos) < tail * 0.7:
                value = 0.0
    elif env == "shutter":
        gap = 0.5 * _triangle(local)
        value = 0.0 if abs(u - 0.5) < gap else 1.0
    elif env == "pulse":
        value = 0.55 + 0.45 * (1.0 - local)
    elif env == "pulse_scan":
        # Segment pulse. The analog pulse floor never goes dark on a bar.
        value = _hit(local, 0.16)
    elif env == "hit":
        value = _hit(local, 0.18)
    elif env == "hit_wide":
        value = _hit(local, 0.32)
    elif env == "double":
        second = _hit((local - 0.5) % 1.0, 0.14) * (0.7 if local >= 0.45 else 0.0)
        value = max(_hit(local, 0.14), second)
    elif env == "double_offbeat":
        second = _hit((local - 0.25) % 1.0, 0.12) * (0.55 if local >= 0.18 else 0.0)
        value = max(_hit(local, 0.12), second)
    elif env == "breathe":
        value = 0.28 + 0.72 * _breathe(local)
    elif env == "compound_breathe_hit":
        value = 0.22 * _breathe(local) + 0.78 * _hit(local, 0.16)
    elif env == "compound_fill_band":
        fill = 1.0 if u <= _saw(local) else 0.0
        band = max(0.0, 1.0 - abs(u - (local * 2.0) % 1.0) / 0.14)
        value = max(0.35 * fill, band)
    elif env == "compound_sine_cascade":
        steps = 8
        index = int(u * (steps - 1) + 1e-6)
        cascade = 1.0 if index <= int(_saw(local) * steps) else 0.0
        # A 0.35 sine never crossed the bar on/off threshold, so this look
        # painted the same DMX as cascade_up. The sine band is binary-visible.
        sine = 0.5 + 0.5 * math.sin(2.0 * math.pi * (u * 2.0 - local))
        sine_band = 1.0 if sine >= 0.62 else 0.0
        value = max(cascade, sine_band)
    elif env == "compound_negative_window":
        bg = 0.85
        pos = local
        value = 0.0 if abs(u - pos) < 0.12 else bg
    elif env == "compound_wave_accent":
        height = _triangle(local)
        wave = 1.0 if u <= height + 0.02 else 0.0
        value = max(wave * 0.55, _hit(local, 0.16))
    elif env == "density":
        span = _mirror_x(cell) if cell.kind in {FixtureKind.PAR, FixtureKind.BEAM} else cell.x
        seed = (cell.bar_index * 17 + cell.segment * 13 + int(span * 11) + 3) % 100
        value = 1.0 if seed / 99.0 <= _saw(local) + 0.02 else 0.0
    else:
        value = _triangle(local)
    if item.polarity == "negative":
        value = 1.0 - value
    gate = _role_gate(cell, item.roles, phase)
    return round(max(0.0, min(1.0, value * gate)), 6)


def _binary(value: float) -> float:
    return 1.0 if value >= 0.42 else 0.0


def paint(
    item: Composition,
    canvas: Canvas,
    *,
    phase: float,
    amount: float,
    cap: float,
    color: Rgbw,
    strobe: float,
    palette: str | None = None,
) -> dict[str, ParIntent | BarIntent | BeamIntent]:
    """Paint one composition onto canvas cells. Calibration is not applied here."""
    allowed = {_kind(name) for name in item.groups}
    phase = float(phase) % 1.0
    amount = max(0.0, min(float(cap), float(amount)))
    bar_count = max(1, len(canvas.bar_ids))
    levels: dict[tuple[str, int], float] = {}
    dimmers: dict[str, float] = {}
    for cell in canvas.cells:
        if cell.kind not in allowed:
            continue
        geometry = _field(item, cell, phase, bar_count)
        key = (cell.fixture_id, cell.segment)
        levels[key] = max(levels.get(key, 0.0), geometry)
        dimmers[cell.fixture_id] = max(dimmers.get(cell.fixture_id, 0.0), geometry)
    stage: dict[str, ParIntent | BarIntent | BeamIntent] = {}
    for fixture_id, cells in canvas.by_fixture.items():
        kind = cells[0].kind
        if kind not in allowed:
            continue
        peak = dimmers.get(fixture_id, 0.0)
        first = cells[0]
        painted = (
            color_for_fixture(palette, kind=kind, x=first.x, bar_index=first.bar_index)
            if palette
            else color
        )
        if kind is FixtureKind.BAR:
            count = len(cells)
            patterned = item.needs_segments
            if patterned:
                bits = [0.0] * max(count, 1)
                for cell in cells:
                    bits[cell.segment] = _binary(levels.get((fixture_id, cell.segment), 0.0))
                if amount <= 1e-9:
                    bits = [0.0] * max(count, 1)
                stage[fixture_id] = BarIntent(
                    segments=tuple(bits),
                    dimmer=0.0 if amount <= 1e-9 else min(cap, amount),
                    color=painted,
                    strobe=strobe,
                    whole=False,
                )
            else:
                if amount <= 1e-9:
                    stage[fixture_id] = BarIntent(
                        segments=tuple(0.0 for _ in range(max(count, 1))),
                        dimmer=0.0,
                        color=painted,
                        strobe=strobe,
                        whole=True,
                    )
                    continue
                if peak < 0.05:
                    continue
                stage[fixture_id] = BarIntent(
                    segments=tuple(1.0 for _ in range(max(count, 1))),
                    dimmer=min(cap, peak * amount),
                    color=painted,
                    strobe=strobe,
                    whole=True,
                )
        elif kind is FixtureKind.PAR:
            if amount <= 1e-9:
                stage[fixture_id] = ParIntent(color=painted, intensity=0.0, strobe=strobe)
                continue
            if peak < 0.04:
                continue
            stage[fixture_id] = ParIntent(
                color=painted, intensity=min(cap, peak * amount), strobe=strobe
            )
        elif kind is FixtureKind.BEAM:
            beam_on = 1.0 if peak >= 0.16 else 0.0
            if beam_on <= 0.0:
                continue
            stage[fixture_id] = BeamIntent(
                pan=None,
                tilt=None,
                dimmer=beam_on,
                color=painted,
                strobe=0.0,
            )
    return stage


def describe(item: Composition) -> dict[str, object]:
    family = next((entry for entry in FAMILIES if entry.id == item.family), None)
    return {
        "id": item.id,
        "family": item.family,
        "family_title": None if family is None else family.title,
        "title": item.title,
        "groups": list(item.groups),
        "palettes": list(item.palettes),
        "motion": None if family is None else family.motion,
        "envelope": item.envelope,
        "axis": item.axis,
        "direction": item.direction,
        "stagger": item.stagger,
        "roles": item.roles,
        "cycles": list(item.cycles),
        "durations": list(item.durations),
        "transition": item.transition,
        "pool": item.pool,
        "needs_segments": item.needs_segments,
        "notes": item.notes,
        "constraints": item.constraints,
        "program": item.program,
    }
