"""Decode a small set of sprites from an installed S&F client for local viewing.

Requires UnityPy. Output is ignored by Git; game artwork is not part of the
project's MIT license. This tool never downloads assets or contacts game servers.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("web/local-assets"))
    parser.add_argument("--dependency-dir", type=Path)
    parser.add_argument("--vod-assets", action="store_true", help="Also decode right-facing doors and trap sprites for offline video recognition")
    parser.add_argument("--cursors-only", action="store_true", help="Add interaction cursors to an existing local art manifest")
    parser.add_argument("--fonts-only", action="store_true", help="Add the installed client's reward font to an existing local art manifest")
    parser.add_argument("--hud-only", action="store_true", help="Add the original stone sockets and HUD art to an existing manifest")
    parser.add_argument("--icons-only", action="store_true", help="Add the installed mushroom and backpack icons to an existing manifest")
    args = parser.parse_args()
    if args.dependency_dir:
        sys.path.insert(0, str(args.dependency_dir.resolve(strict=True)))
    import UnityPy
    source = args.game_dir / "shakesandfidget_Data/StreamingAssets"
    if not (source / "dot/doors").is_file():
        parser.error("No dungeon bundles found in this game directory")
    target = args.output_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    wanted: dict[str, dict[str, list[str]]] = {}

    def add(bundle: str, sprite: str, *keys: str) -> None:
        wanted.setdefault(bundle, {})[sprite] = list(keys)

    doors = {"monster":"monster", "mystery":"unknown", "locked":"closed", "double_locked":"double",
             "unlocked":"closed_half", "epic":"closed_epic", "shop":"shop", "cursed":"curse",
             "sacrifice":"hp", "blessing":"bless", "destiny":"fortune", "boss":"boss", "wall":"blocked",
             "trial":"arena", "exit_trial":"arena_exit", "hungry":"hungry"}
    for kind, suffix in doors.items():
        add("dot/doors", "dot_left_" + suffix, "door." + kind)
    add("dot/doorsspecial", "dot_left_special", "door.golden")
    add("dot/traps", "dot_trap_left_beartrap", "trap.beartrap")
    add("dot/traps", "dot_trap_right_beartrap", "trap_right.beartrap")
    if args.vod_assets:
        for kind, suffix in [("mystery", "unknown"), ("cursed", "curse"), ("blessing", "bless")]:
            add("dot/doors", "dot_right_" + suffix, "door_right." + kind)
        for kind in ["cleaver", "bucket", "beartrap", "spear", "woodspear", "hammer", "pendulum", "tripwire", "shark"]:
            add("dot/traps", "dot_trap_left_" + kind, "trap." + kind)
        add("dot/traps", "dot_trap_right_beartrap", "trap_right.beartrap")
    # Matched by icon against the player's community stone chart (2026-10-07).
    gems = {"rabbit":2,"moonstone":18,"spying":9,"pendant":6,"greasy":8,"gambler":11,
            "greed":3,"hero":20,"time_traveler":19,"thirsty":15,"pearl":22,"blood":13,
            "explorer":4,"misadventurer":17,"devil":21,"deceit":7,"lodestone":10,
            "bull":1,"hick":16,"rusty":23}
    for name, number in gems.items():
        add("dot/gemsoffate", f"dot_all_gemoffate_{number:02d}", "gem." + name)
    for stage in range(1, 5):
        for mode, name in [("doors", "decision"), ("encounter", "interact")]:
            add(f"dot/cthulhu_rooms0{stage}", f"dot_cthulhu_{name}_0{stage}0", f"background.{stage}.{mode}")
        add(f"monsters_dot/m{5296+stage:04d}_dot", f"monster{5296+stage}", f"boss.{stage}")
    add("monsters_dot/m0001_dot", "monster1", "encounter.monster", "encounter.trial_monster")
    add("monsters_dot/m5002_dot", "monster5002", "encounter.undead")
    add("monsters_dot/m5000_dot", "monster5000", "encounter.soldier_awake")
    effects = ["raider","one_hit","escape","disarm","lockpick","key_moment","elixir","recovery"]
    for i, name in enumerate(effects, 1): add("dot/buffs", f"dot_all_buff_{i:02d}", "effect." + name)
    for i, name in enumerate(["broken_armor","poison","clumsy","gold_hangover","hard_lock"], 1):
        add("dot/buffs", f"dot_all_debuff_{i:02d}", "effect." + name)
    containers = {"bronze":0,"silver":1,"epic":2,"crate":100,"skeleton":300,"barrel":400,
                  "mimic":500,"sacrifice_chest":600,"cursed_chest":601,"prize":602,"sated_chest":603}
    for name, number in containers.items():
        add(f"container/c{number}", f"container{number}_closed", "encounter." + name)
        if number in (0, 1, 2, 100, 300):
            add(f"container/c{number}", f"container{number}_open", "opened." + name)
    for number, sprite, names in [
        ("01_0","fountainlife",["fountain"]), ("01_2","fountainlifedebuff",["cleansing_fountain"]),
        ("03","rocks_blocked",["rocks"]), ("04","lava_base",["lava"]), ("05","narrator",["narrator"]),
        ("07","wishing_well",["wishing_well"]), ("09","sewer",["sewers"]),
        ("11","cityguard_lockerclosed",["locker"]), ("12_0","sarcophagus_closed",["sarcophagus"]),
        ("12_2","sarcophaguslocked_closed",["locked_sarcophagus"]), ("14","wood_blocked",["wood"]),
        ("17","spider_part1",["spider_web"]), ("17","spider_part2",["spider_full"]),
        ("17","spider_key1",["spider_key1"]), ("17","spider_key2",["spider_key2"]), ("17","spider_key3",["spider_key3"]),
        ("19","beta",["beta"]), ("21","souls_blocked",["souls"]), ("22","arcane_blocked",["arcane"]),
        ("23","shopdebuff",["curse_shop"]), ("29","weaponchamber_clear",["armory"])]:
        add("dot/specialroom" + number, "dot_all_special_" + sprite, *("encounter." + n for n in names))
    add("dot/specialroom15", "dot_shopkeeper", "shopkeeper")
    add("dot/specialroom08", "dot_all_rps", "encounter.rps")
    add("dot/specialroom16_0", "wheel_tier1", "encounter.wheel")
    # Fixed reference items; no particular inventory item is rolled by the model.
    # Client Item.IsLegendary: item type <11 and index >=100. Epic index 50
    # comes from the same equipment atlas; keep the three rarities distinct.
    add("items/warriorweapons", "itm1_1_1_1", "reward.item")
    add("items/warriorweapons", "itm1_50_1_1", "reward.epic")
    add("items/legendarieshuge/itm1_100_1_1", "itm1_100_1_1", "reward.legendary")
    manifest = {"schema":1, "source":"local installed S&F client", "theme":"cthulhu",
                "license":"Game artwork belongs to its owners; local use only, excluded from this repository's MIT license.",
                "assets":{}, "bundles":{}, "missing":[]}
    add("dot/ui", "dot_gemoffate_sockets", "hud.stone_sockets")
    add("dot/ui", "dot_gemoffate_info", "hud.stone_frame")
    if (args.cursors_only or args.fonts_only or args.hud_only or args.icons_only) and (target / 'manifest.json').exists():
        manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
    if args.cursors_only or args.fonts_only or args.icons_only:
        wanted = {}
    elif args.hud_only:
        wanted = {"dot/ui": {"dot_gemoffate_sockets": ["hud.stone_sockets"], "dot_gemoffate_info": ["hud.stone_frame"]}}
    for bundle, sprites in wanted.items():
        path = source / bundle
        if not path.is_file():
            manifest["missing"].append(bundle); continue
        env = UnityPy.load(str(path))
        found = set()
        for obj in env.objects:
            if obj.type.name != "Sprite": continue
            sprite = obj.read()
            if sprite.m_Name not in sprites: continue
            filename = bundle.replace("/", "_") + "-" + sprite.m_Name + ".png"
            sprite.image.save(target / filename)
            for key in sprites[sprite.m_Name]: manifest["assets"][key] = filename
            found.add(sprite.m_Name)
        manifest["missing"].extend(bundle + ":" + name for name in sprites if name not in found)
        manifest["bundles"][bundle] = hashlib.sha256(path.read_bytes()).hexdigest()
    icon_source = source.parent / 'resources.assets'
    if icon_source.exists() and not (args.fonts_only or args.cursors_only):
        icons = {'resource_mushroom_icon':'hud.mushroom', 'backpack_icon':'hud.backpack'}
        found = set()
        for obj in UnityPy.load(str(icon_source)).objects:
            if obj.type.name != 'Sprite': continue
            sprite = obj.read()
            if sprite.m_Name not in icons: continue
            filename = 'resources-' + sprite.m_Name + '.png'
            sprite.image.save(target / filename)
            manifest['assets'][icons[sprite.m_Name]] = filename
            found.add(sprite.m_Name)
        manifest['missing'].extend('resources.assets:' + name for name in icons if name not in found)
        manifest['bundles']['../resources.assets'] = hashlib.sha256(icon_source.read_bytes()).hexdigest()
    # Dungeon cursors live in the built-in Unity scene, not StreamingAssets.
    # Native CSS cursors need a small canvas; keep the game sprite proportions.
    from PIL import Image, ImageOps
    cursor_source = source.parent / 'sharedassets1.assets'
    cursor_names = {'icon_flee':'flee', 'icon_interact':'interact', 'icon_fight':'fight', 'icon_arrow':'arrow'}
    if cursor_source.exists():
        found = set()
        for obj in UnityPy.load(str(cursor_source)).objects:
            if obj.type.name == 'Font':
                font = obj.read()
                if font.m_Name == 'RodrigoTypo - Tobi' and font.m_FontData:
                    filename = 'sf-tobi.ttf'
                    (target / filename).write_bytes(bytes(font.m_FontData))
                    manifest['assets']['font.reward'] = filename
                continue
            if args.fonts_only or obj.type.name != 'Sprite':
                continue
            sprite = obj.read()
            if sprite.m_Name not in cursor_names:
                continue
            name = cursor_names[sprite.m_Name]
            bitmap = sprite.image.convert('RGBA')
            bitmap.thumbnail((54, 54), Image.Resampling.LANCZOS)
            canvas = Image.new('RGBA', (64, 64))
            canvas.alpha_composite(bitmap, ((64-bitmap.width)//2, (64-bitmap.height)//2))
            filename = f'cursor-{name}.png'
            canvas.save(target / filename)
            manifest['assets']['cursor.' + name] = filename
            if name == 'arrow':
                ImageOps.mirror(canvas).save(target / 'cursor-arrow-left.png')
                # The native arrow points left. Keep the old filename for
                # existing local assets, but expose correctly named directions.
                manifest['assets']['cursor.arrow_left'] = filename
                manifest['assets']['cursor.arrow_right'] = 'cursor-arrow-left.png'
            found.add(sprite.m_Name)
        if not args.fonts_only:
            manifest['missing'].extend('sharedassets1.assets:' + name for name in cursor_names if name not in found)
        manifest['bundles']['../sharedassets1.assets'] = hashlib.sha256(cursor_source.read_bytes()).hexdigest()
        resource = cursor_source.with_suffix('.assets.resS')
        if resource.exists():
            manifest['bundles']['../sharedassets1.assets.resS'] = hashlib.sha256(resource.read_bytes()).hexdigest()
    else:
        manifest['missing'].append('sharedassets1.assets:cursors')
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Decoded {len(manifest['assets'])} named assets; {len(manifest['missing'])} missing. Output: {target}")
    if manifest["missing"]: print("Missing:", ", ".join(manifest["missing"]))


if __name__ == "__main__": main()
