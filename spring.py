import os
import sys
import time
import random
from dataclasses import dataclass
from colorama import init, Fore, Back, Style

init()

# =========================
# Spring Garden Automata (no commands, pure sim)
# =========================

WIDTH = 80
HEIGHT = 20
TICK_SLEEP = 0.16  # lower = faster

# Plant chars (yours)
GRASS_SHORT = "."
GRASS_LONG  = ","
GRASS_TALL  = ";"
SHRUB       = "&"
FLOWER1     = "@"
FLOWER2     = "%"
FLOWER3     = "*"
PLANT_TYPES = [GRASS_SHORT, GRASS_LONG, GRASS_TALL, SHRUB, FLOWER1, FLOWER2, FLOWER3]

# Effect overlay chars
RAIN_CHAR = "'"
WIND_CHAR = ">"


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


@dataclass
class PlantSpec:
    moist_min: int
    moist_max: int
    soil_min: int
    grow_chance: float
    flower_chance: float
    seed_drop_chance: float
    driftiness: float


PLANT_SPECS = {
    GRASS_SHORT: PlantSpec(2, 7, 2, 0.35, 0.05, 0.30, 0.80),
    GRASS_LONG:  PlantSpec(3, 8, 3, 0.30, 0.06, 0.35, 0.75),
    GRASS_TALL:  PlantSpec(4, 8, 4, 0.25, 0.07, 0.40, 0.70),
    SHRUB:       PlantSpec(3, 7, 5, 0.18, 0.08, 0.35, 0.45),
    FLOWER1:     PlantSpec(3, 7, 4, 0.22, 0.14, 0.45, 0.60),
    FLOWER2:     PlantSpec(2, 6, 4, 0.20, 0.16, 0.45, 0.55),
    FLOWER3:     PlantSpec(4, 8, 5, 0.18, 0.18, 0.50, 0.50),
}


@dataclass
class Cell:
    plant: str = " "     # plant char or " "
    stage: int = 0       # 0..3
    health: int = 0      # 0..9
    moisture: int = 0    # 0..9
    soil: int = 0        # 0..9
    flowering: bool = False
    seed: str = " "      # pending seed type
    seed_age: int = 0


SEASONS = ["Spring", "Summer", "Autumn", "Winter"]


def season_params(name: str):
    """
    Returns:
      rain_start_chance: chance per tick to start a rain event when not raining
      rain_min_ticks, rain_max_ticks: duration of rain event
      rain_drops_per_tick: intensity
      evap_max: random 0..evap_max moisture removed per tick
      wind_strength: probability factor for seed drift
      growth_bonus: multiplies plant growth / flowering
      bee_activity: affects pollination help
    """
    if name == "Spring":
        return dict(rain_start_chance=0.030, rain_min_ticks=10, rain_max_ticks=40, rain_drops_per_tick=30,
                    evap_max=1, wind_strength=0.12, growth_bonus=1.15, bee_activity=1.00)
    if name == "Summer":
        # gentler summer: not a desert. still rains sometimes, evap not brutal.
        return dict(rain_start_chance=0.018, rain_min_ticks=8, rain_max_ticks=25, rain_drops_per_tick=18,
                    evap_max=1, wind_strength=0.10, growth_bonus=1.00, bee_activity=0.90)
    if name == "Autumn":
        return dict(rain_start_chance=0.020, rain_min_ticks=10, rain_max_ticks=30, rain_drops_per_tick=22,
                    evap_max=1, wind_strength=0.18, growth_bonus=0.95, bee_activity=0.80)
    # Winter
    return dict(rain_start_chance=0.010, rain_min_ticks=6, rain_max_ticks=18, rain_drops_per_tick=12,
                evap_max=1, wind_strength=0.10, growth_bonus=0.60, bee_activity=0.35)


def neighbours4(x, y):
    return [(x-1, y), (x+1, y), (x, y-1), (x, y+1)]


def neighbours8(x, y):
    pts = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            pts.append((x + dx, y + dy))
    return pts


def in_bounds(x, y):
    return 0 <= x < WIDTH and 0 <= y < HEIGHT


def make_grid():
    g = [[Cell() for _ in range(WIDTH)] for _ in range(HEIGHT)]

    # initialise soil + moisture with gentle noise
    for y in range(HEIGHT):
        for x in range(WIDTH):
            c = g[y][x]
            c.soil = clamp(int(random.gauss(5, 1.4)), 0, 9)
            c.moisture = clamp(int(random.gauss(4, 1.6)), 0, 9)

    # sprinkle initial grasses
    for _ in range(int(WIDTH * HEIGHT * 0.10)):
        x = random.randrange(WIDTH)
        y = random.randrange(HEIGHT)
        plant = random.choice([GRASS_SHORT, GRASS_LONG, GRASS_TALL, " ", " "])
        if plant != " ":
            place_plant(g, x, y, plant, initial=True)

    # some flowers
    for _ in range(26):
        x = random.randrange(WIDTH)
        y = random.randrange(HEIGHT)
        place_plant(g, x, y, random.choice([FLOWER1, FLOWER2, FLOWER3]), initial=True)

    # some shrubs
    for _ in range(14):
        x = random.randrange(WIDTH)
        y = random.randrange(HEIGHT)
        place_plant(g, x, y, SHRUB, initial=True)

    return g


def place_plant(grid, x, y, plant_type, initial=False):
    c = grid[y][x]
    c.plant = plant_type
    c.stage = random.randint(1, 2) if initial else 0
    c.health = random.randint(5, 8) if initial else 6
    c.flowering = False
    c.seed = " "
    c.seed_age = 0


# ========= Rendering (no flicker) =========

def start_terminal():
    # clear once + hide cursor
    sys.stdout.write("\x1b[2J\x1b[?25l")
    sys.stdout.flush()


def stop_terminal():
    # show cursor
    sys.stdout.write("\x1b[?25h" + Style.RESET_ALL)
    sys.stdout.flush()


def begin_frame():
    # home + clear to end
    sys.stdout.write("\x1b[H")


def bg_for_cell(c: Cell):
    # keep background consistent: soil quality & dampness vibe
    if c.moisture <= 0:
        return Back.YELLOW + Style.DIM
    if c.soil >= 6 and c.moisture >= 4:
        return Back.GREEN + Style.DIM
    return Back.GREEN + Style.DIM


def fg_for_plant(c: Cell):
    # Your palette idea:
    # healthy -> green bright; medium -> green dim; unhealthy -> yellow;
    # flowering -> red
    if c.flowering:
        return (Fore.RED + Style.BRIGHT) if c.health >= 6 else (Fore.RED + Style.DIM)

    if c.health >= 7:
        return Fore.GREEN + Style.BRIGHT
    if c.health >= 4:
        return Fore.GREEN + Style.DIM
    if c.health >= 2:
        return Fore.YELLOW + Style.BRIGHT
    return Fore.YELLOW + Style.DIM


def render(grid, season, day, wind_band_x, rain_positions):
    begin_frame()

    # minimal header (1 line only)
    sys.stdout.write(f"{Fore.CYAN}{Style.BRIGHT}{season}{Style.RESET_ALL}  Day {day}\n")

    rain_set = set(rain_positions)

    # Build all lines, write once (smooth)
    out_lines = []
    for y in range(HEIGHT):
        row_parts = []
        for x in range(WIDTH):
            c = grid[y][x]
            bg = bg_for_cell(c)

            # overlays (do NOT mutate background/state)
            if (x, y) in rain_set:
                row_parts.append(bg + Fore.CYAN + Style.BRIGHT + RAIN_CHAR + Style.RESET_ALL)
                continue

            if x == wind_band_x and random.random() < 0.20:
                row_parts.append(bg + Fore.WHITE + Style.DIM + WIND_CHAR + Style.RESET_ALL)
                continue

            if c.plant != " ":
                row_parts.append(bg + fg_for_plant(c) + c.plant + Style.RESET_ALL)
            else:
                # show seed faintly
                if c.seed != " ":
                    row_parts.append(bg + Fore.WHITE + Style.DIM + "·" + Style.RESET_ALL)
                else:
                    row_parts.append(bg + " " + Style.RESET_ALL)

        out_lines.append("".join(row_parts))

    sys.stdout.write("\n".join(out_lines) + "\n")
    sys.stdout.flush()


# ========= Simulation tick =========

def tick(grid, season, wind_band_x, bees, worms, rain_timer):
    p = season_params(season)

    # --- rain events ---
    if rain_timer <= 0 and random.random() < p["rain_start_chance"]:
        rain_timer = random.randint(p["rain_min_ticks"], p["rain_max_ticks"])

    rain_positions = []
    if rain_timer > 0:
        rain_timer -= 1
        # produce overlay positions AND also increase moisture in those cells
        for _ in range(p["rain_drops_per_tick"]):
            x = random.randrange(WIDTH)
            y = random.randrange(HEIGHT)
            rain_positions.append((x, y))
            grid[y][x].moisture = clamp(grid[y][x].moisture + random.randint(1, 3), 0, 9)

    # --- evaporation (gentle) ---
    evap_max = p["evap_max"]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            grid[y][x].moisture = clamp(grid[y][x].moisture - (random.randint(0, evap_max)/40), 0, 9)

    # --- worms (soil improvement) ---
    new_worms = []
    for (wx, wy) in worms:
        grid[wy][wx].soil = clamp(grid[wy][wx].soil + 1, 0, 9)
        dx, dy = random.choice([(1,0),(-1,0),(0,1),(0,-1),(0,0)])
        nx, ny = wx + dx, wy + dy
        if in_bounds(nx, ny):
            new_worms.append((nx, ny))
        else:
            new_worms.append((wx, wy))
    worms[:] = new_worms

    # --- bees (pollination help) ---
    # bees wander; if they land on flowering plants they slightly boost health & seed success indirectly
    new_bees = []
    for (bx, by) in bees:
        candidates = neighbours8(bx, by) + [(bx, by)]
        candidates = [(x, y) for (x, y) in candidates if in_bounds(x, y)]

        scored = []
        for (cx, cy) in candidates:
            c = grid[cy][cx]
            score = 1.0
            if c.plant in (FLOWER1, FLOWER2, FLOWER3, SHRUB):
                score += 2.0
                if c.flowering:
                    score += 3.0
            scored.append((score, cx, cy))

        total = sum(s for s, _, _ in scored)
        r = random.random() * total
        acc = 0.0
        nx, ny = bx, by
        for s, cx, cy in scored:
            acc += s
            if r <= acc:
                nx, ny = cx, cy
                break

        c = grid[ny][nx]
        if c.plant in (FLOWER1, FLOWER2, FLOWER3, SHRUB):
            if random.random() < 0.22 * p["bee_activity"]:
                c.health = clamp(c.health + 1, 0, 9)

        new_bees.append((nx, ny))
    bees[:] = new_bees

    # --- wind: drift seeds ---
    wind_dir = 1
    if season == "Autumn" and random.random() < 0.35:
        wind_dir = -1

    seed_moves = []
    for y in range(HEIGHT):
        for x in range(WIDTH):
            c = grid[y][x]
            if c.seed == " ":
                continue
            spec = PLANT_SPECS.get(c.seed, PLANT_SPECS[GRASS_SHORT])
            local_strength = p["wind_strength"] + (0.25 if x == wind_band_x else 0.0)
            drift_prob = local_strength * spec.driftiness
            if random.random() < drift_prob:
                nx, ny = x + wind_dir, y
                if in_bounds(nx, ny):
                    t = grid[ny][nx]
                    if t.plant == " " and t.seed == " ":
                        seed_moves.append((x, y, nx, ny))

    for x, y, nx, ny in seed_moves:
        grid[ny][nx].seed = grid[y][x].seed
        grid[ny][nx].seed_age = 0
        grid[y][x].seed = " "
        grid[y][x].seed_age = 0

    # --- plant lifecycle ---
    growth_bonus = p["growth_bonus"]

    for y in range(HEIGHT):
        for x in range(WIDTH):
            c = grid[y][x]

            # seeds sprout
            if c.plant == " " and c.seed != " ":
                c.seed_age += 1
                spec = PLANT_SPECS[c.seed]

                ok_moist = spec.moist_min <= c.moisture <= spec.moist_max
                ok_soil = c.soil >= spec.soil_min

                sprout = 0.05
                if ok_moist and ok_soil:
                    sprout = 0.18 * growth_bonus
                if season == "Winter" or season == "Autumn" :
                    sprout *= 0.45

                if random.random() < sprout:
                    place_plant(grid, x, y, c.seed, initial=False)

                if c.seed_age > 26 and random.random() < 0.18:
                    c.seed = " "
                    c.seed_age = 0

            if c.plant == " ":
                continue

            spec = PLANT_SPECS[c.plant]

            # health changes from moisture + soil
            if c.moisture < spec.moist_min and random.random() < 0.35:
                c.health -= 1
            elif c.moisture > spec.moist_max and random.random() < 0.35:
                c.health -= 1
            else:
                if random.random() < 0.28 * growth_bonus:
                    c.health += 1

            if c.soil < spec.soil_min and random.random() < 0.35:
                c.health -= 1
            else:
                if random.random() < 0.18 * growth_bonus:
                    c.health += 1
            # --- autumn stress ---
            if season == "Autumn":
                # small passive decay
                if random.random() < 0.25:
                    c.health -= 1

                # extra stress for flowering plants
                if c.flowering and random.random() < 0.40:
                    c.health -= 1

            # --- winter stress ---
            if season == "Winter":
                # small passive decay
                if random.random() < 0.4:
                    c.health -= 1

                # extra stress for flowering plants
                if c.flowering and random.random() < 0.40:
                    c.health -= 1
        
            c.health = clamp(c.health, 0, 9)

            # growth stage
            if c.stage < 3 and random.random() < (spec.grow_chance * growth_bonus):
                if (season != "Winter" and season != "Autumn" ) or random.random() < 0.25:
                    c.stage += 1

            # flowering
            c.flowering = False
            if c.plant in (FLOWER1, FLOWER2, FLOWER3, SHRUB):
                if c.stage >= 2 and c.health >= 4:
                    if random.random() < (spec.flower_chance * growth_bonus * p["bee_activity"]):
                        c.flowering = True

            # seed drop
            if c.flowering and random.random() < spec.seed_drop_chance:
                nbrs = neighbours8(x, y)
                random.shuffle(nbrs)
                for nx, ny in nbrs:
                    if not in_bounds(nx, ny):
                        continue
                    t = grid[ny][nx]
                    if t.plant == " " and t.seed == " ":
                        t.seed = c.plant
                        t.seed_age = 0
                        break

            # death -> enrich soil a bit
            if c.health <= 0:
                c.plant = " "
                c.stage = 0
                c.flowering = False
                c.seed = " "
                c.seed_age = 0
                c.soil = clamp(c.soil + random.randint(1, 2), 0, 9)

    # --- tiny moisture diffusion (pretty patterns) ---
    for _ in range(80):  # a few random micro-diffusions
        x = random.randrange(WIDTH)
        y = random.randrange(HEIGHT)
        c = grid[y][x]
        for nx, ny in neighbours4(x, y):
            if not in_bounds(nx, ny):
                continue
            n = grid[ny][nx]
            if c.moisture - n.moisture >= 3 and random.random() < 0.35:
                c.moisture -= 1
                n.moisture += 1

    # advance wind band
    wind_band_x = (wind_band_x + 1) % WIDTH

    return wind_band_x, rain_positions, rain_timer


def main():
    start_terminal()

    grid = make_grid()
    bees = [(random.randrange(WIDTH), random.randrange(HEIGHT)) for _ in range(18)]
    worms = [(random.randrange(WIDTH), random.randrange(HEIGHT)) for _ in range(14)]

    day = 1
    season_index = 0
    SEASON_LENGTH = 80  # ticks per season
    wind_band_x = 0
    rain_timer = 0
    rain_positions = []

    try:
        while True:
            season = SEASONS[season_index]

            wind_band_x, rain_positions, rain_timer = tick(
                grid, season, wind_band_x, bees, worms, rain_timer
            )

            render(grid, season, day, wind_band_x, rain_positions)

            day += 1
            if day % SEASON_LENGTH == 0:
                season_index = (season_index + 1) % len(SEASONS)

            time.sleep(TICK_SLEEP)

    except KeyboardInterrupt:
        pass
    finally:
        stop_terminal()
        print("Bye 🌱")


if __name__ == "__main__":
    main()
