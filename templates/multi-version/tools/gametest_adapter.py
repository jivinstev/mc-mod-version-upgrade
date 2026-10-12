#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Jason Hendrickson
"""Adapt annotation-driven @GameTests to Minecraft 26.x's registration model.

WHAT CHANGED
------------
Through 1.21.x a GameTest was discovered from annotations: @GameTestHolder on the class,
@GameTest on each `public static void x(GameTestHelper)`. In 26.x that annotation does not
exist -- not in vanilla, not in NeoForge, whose gametest package is down to GameTestHooks.
Discovery is now registration:

  * the FUNCTION  -- a Consumer<GameTestHelper> in the Registries.TEST_FUNCTION registry,
                     supplied by a TestFunctionLoader;
  * the INSTANCE  -- a GameTestInstance (here FunctionGameTestInstance) carrying TestData
                     (structure, max ticks, environment), registered from NeoForge's
                     RegisterGameTestsEvent.

WHY GENERATE IT
The test BODIES are unchanged -- a test is still a method taking a GameTestHelper -- so the
only thing missing is the wiring. Rewriting 56 files by hand to say what the annotations
already say would be 241 chances to typo a name, and it would have to be redone every time
a test is added. Generating the wiring from the annotations keeps ONE source of truth: the
shared tree stays annotated (which is what 1.21.1 needs), and the 26.x copy gets a
registrar derived from those same annotations.

So this runs over the PREPARED 26.x tree: it strips the annotations that no longer exist
and emits the registrar that replaces them.
"""
import os, re, sys

# @GameTest(...) [more annotations] [modifiers] void name(GameTestHelper ...)
TEST_RE = re.compile(
    r'@GameTest\b(?:\s*\(([^)]*)\))?\s*'
    r'((?:@[\w.]+(?:\([^)]*\))?\s*)*)'
    r'((?:public|protected|private|static|final|\s)*?)'
    r'void\s+(\w+)\s*\(\s*(?:final\s+)?GameTestHelper\b',
    re.S)

STRIP_ANNOTATION_RE = re.compile(
    r'^[ \t]*@(?:GameTest|GameTestHolder|PrefixGameTestTemplate)\b(?:\s*\([^)]*\))?[ \t]*\r?\n',
    re.M)

# Only the annotation imports go; GameTestHelper and friends must survive.
STRIP_IMPORT_RE = re.compile(
    r'^import\s+(?:net\.minecraft\.gametest\.framework\.GameTest'
    r'|net\.neoforged\.neoforge\.gametest\.GameTestHolder'
    r'|net\.neoforged\.neoforge\.gametest\.PrefixGameTestTemplate)\s*;[ \t]*\r?\n',
    re.M)

# The registration model arrived in 1.21.5; 26.x kept it but renamed ResourceLocation->Identifier and made
# TestEnvironmentDefinition generic. Both dialects are emitted from one template (verified against the 1.21.5 and
# 26.2 sources: GameTestInstance, TestData, RegisterGameTestsEvent, Registries.TEST_INSTANCE_TYPE).
DIALECTS = {
    '26': dict(ident='Identifier', ident_import='net.minecraft.resources.Identifier',
               envt='TestEnvironmentDefinition<?>', bus=''),
    # 1.21.5: FML's @EventBusSubscriber still has a bus() (no auto-detection), and both events are mod-bus events.
    '1.21.5': dict(ident='ResourceLocation', ident_import='net.minecraft.resources.ResourceLocation',
                   envt='TestEnvironmentDefinition', bus=', bus = EventBusSubscriber.Bus.MOD'),
    # 1.21.6+ (FML 9): @EventBusSubscriber has no bus() any more (inferred from the event type); otherwise as 1.21.5.
    '1.21.8': dict(ident='ResourceLocation', ident_import='net.minecraft.resources.ResourceLocation',
                   envt='TestEnvironmentDefinition', bus=''),
    # 1.21.11: ResourceLocation is Identifier (CATALOG V4) but TestEnvironmentDefinition is still not generic (26.x made it so); FML as 1.21.8.
    '1.21.11': dict(ident='Identifier', ident_import='net.minecraft.resources.Identifier',
                    envt='TestEnvironmentDefinition', bus=''),
}

TIMEOUT_RE = re.compile(r'timeoutTicks\s*=\s*(\d+)')


def snake(name):
    """camelCase -> snake_case. A Minecraft Identifier path may only contain
    [a-z0-9_.-/], so a method name cannot be used as an id verbatim."""
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()


def test_id(fqcn, method):
    """`<class>/<method>`, both snake_cased.

    NOT the bare method name: 9 of this mod's test names are reused across classes
    (contentsSurviveSaveAndLoad, everyStyleIsComplete, ...), which the annotation model
    tolerated because it namespaced by class. A flat id would silently register one and
    shadow the other -- a test that stops running while the count still looks plausible.
    """
    return f"{snake(fqcn.rsplit('.', 1)[-1])}/{snake(method)}"
TEMPLATE_RE = re.compile(r'template\s*=\s*"([^"]+)"')


def adapt(root, modid, out_pkg, default_structure, default_ticks=100, cls='GeneratedGameTests', dialect='26'):
    tests = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith('.java'):
                continue
            path = os.path.join(dirpath, fn)
            text = open(path, encoding='utf-8').read()
            if '@GameTest' not in text:
                continue
            rel = os.path.relpath(path, root)
            fqcn = rel[:-5].replace(os.sep, '.')
            for m in TEST_RE.finditer(text):
                args, _mid, mods, name = m.group(1) or '', m.group(2), m.group(3), m.group(4)
                t = TIMEOUT_RE.search(args)
                s = TEMPLATE_RE.search(args)
                tests.append({
                    'fqcn': fqcn,
                    'method': name,
                    'static': 'static' in mods,
                    'ticks': int(t.group(1)) if t else default_ticks,
                    'structure': s.group(1) if s else default_structure,
                })
            text = STRIP_ANNOTATION_RE.sub('', text)
            text = STRIP_IMPORT_RE.sub('', text)
            open(path, 'w', encoding='utf-8').write(text)

    # A duplicate id would silently shadow one test with another at registration.
    seen, dupes = set(), []
    for t in tests:
        t['id'] = test_id(t['fqcn'], t['method'])
        if t['id'] in seen:
            dupes.append(t['id'])
        seen.add(t['id'])
    if dupes:
        sys.exit(f"gametest-adapter: duplicate test ids: {sorted(set(dupes))}")
    bad = [t['id'] for t in tests if not re.fullmatch(r'[a-z0-9_./-]+', t['id'])]
    if bad:
        sys.exit(f"gametest-adapter: ids illegal for a Minecraft Identifier: {bad[:5]}")

    write_direct_gametest(root, modid, out_pkg, dialect)
    write_registrar(root, modid, out_pkg, tests, cls, dialect)
    return tests



def write_direct_gametest(root, modid, out_pkg, dialect='26'):
    """Emit the GameTestInstance subclass the registrar refers to.

    Generated rather than hand-copied per port: it is the ONLY workable shape (see the class
    javadoc), and a port that copies it by hand is a port that can get it wrong once.
    """
    pkg_dir = os.path.join(root, *out_pkg.split('.'))
    os.makedirs(pkg_dir, exist_ok=True)
    body = '''package {pkg};

import java.util.function.Consumer;

import com.mojang.serialization.MapCodec;
import com.mojang.serialization.codecs.RecordCodecBuilder;

import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.gametest.framework.GameTestHelper;
import net.minecraft.gametest.framework.GameTestInstance;
import net.minecraft.gametest.framework.TestData;
import net.minecraft.gametest.framework.TestEnvironmentDefinition;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import {ident_import};

/**
 * GENERATED by tools/gametest_adapter.py -- do not edit.
 *
 * <p>A GameTest whose body is a method reference, for a Minecraft that otherwise insists tests
 * be looked up out of a registry.
 *
 * <p><b>Why this exists.</b> MC 26.x dropped the {{@code @GameTest}} annotation and made a test
 * two halves: a {{@code Consumer<GameTestHelper>}} in the {{@code TEST_FUNCTION}} registry, and a
 * {{@code GameTestInstance}} naming it. The obvious port supplies the function through
 * {{@code TestFunctionLoader.registerLoader}} -- and that CANNOT WORK FOR A MOD.
 * {{@code runLoaders}} is called from {{@code BuiltinTestFunctions.bootstrap}}, a registry
 * bootstrap that runs before mod construction, so by the time any mod hook could register a
 * loader the registry is already built. The symptom is exact and misleading: every instance
 * registers, every test is found and attempted, and every one fails with
 * {{@code Trying to access missing test function}}.
 *
 * <p>So skip that registry and hold the method reference directly.
 *
 * <p><b>The codec is NOT optional.</b> {{@code TEST_INSTANCE}} is a SYNCED registry, so joining a
 * world packs every entry -- and packing needs each instance's codec registered in
 * {{@code TEST_INSTANCE_TYPE}}. An unregistered one fails the join with
 * {{@code Unregistered holder in ResourceKey[minecraft:root / minecraft:test_instance_type]}}, the
 * client never enters the world, and the run times out saying "world did not load in time" --
 * which points at worldgen, not at a test-registry codec.
 *
 * <p>What the codec CANNOT carry is the method reference, and it does not pretend to: a decoded
 * instance runs nothing. That is honest rather than lossy -- the only decoder is the CLIENT's copy
 * of a synced registry, and tests execute server-side.
 */
public final class DirectGameTest extends GameTestInstance {{

    public static final MapCodec<DirectGameTest> CODEC = RecordCodecBuilder.mapCodec(inst -> inst
            .group(TestData.CODEC.forGetter(DirectGameTest::info),
                   {ident}.CODEC.fieldOf("name").forGetter(t -> {ident}.parse(t.name)))
            .apply(inst, (data, name) -> new DirectGameTest(data, h -> {{ }}, name.toString())));

    private final Consumer<GameTestHelper> body;
    private final String name;

    public DirectGameTest(TestData<Holder<{envt}>> info,
                          Consumer<GameTestHelper> body, String name) {{
        super(info);
        this.body = body;
        this.name = name;
    }}

    @Override
    public void run(GameTestHelper helper) {{
        body.accept(helper);
    }}

    @Override
    public MapCodec<? extends GameTestInstance> codec() {{
        return CODEC;
    }}

    @Override
    protected MutableComponent typeDescription() {{
        return Component.literal(name);
    }}
}}
'''.format(pkg=out_pkg, modid=modid, **DIALECTS[dialect])
    with open(os.path.join(pkg_dir, 'DirectGameTest.java'), 'w', encoding='utf-8') as f:
        f.write(body)

def write_registrar(root, modid, out_pkg, tests, cls='GeneratedGameTests', dialect='26'):
    d = DIALECTS[dialect]
    pkg_dir = os.path.join(root, *out_pkg.split('.'))
    os.makedirs(pkg_dir, exist_ok=True)
    fns, insts = [], []
    for t in sorted(tests, key=lambda x: (x['fqcn'], x['method'])):
        ref = (f"{t['fqcn']}::{t['method']}" if t['static']
               else f"h -> new {t['fqcn']}().{t['method']}(h)")
        insts.append(
            f'        e.registerTest(id("{t["id"]}"), new DirectGameTest(\n'
            f'                new TestData<>(env, {d["ident"]}.fromNamespaceAndPath(MODID, "{t["structure"]}"),\n'
            f'                        {t["ticks"]}, 0, true),\n'
            f'                {ref}, "{t["id"]}"));')
    body = f'''package {out_pkg};

import java.util.List;

import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.gametest.framework.TestData;
import net.minecraft.gametest.framework.TestEnvironmentDefinition;
import {d['ident_import']};
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.RegisterGameTestsEvent;
import net.neoforged.neoforge.registries.RegisterEvent;

/**
 * GENERATED by tools/gametest_adapter.py -- do not edit.
 *
 * <p>Minecraft 26.x removed the @GameTest annotation, so the tests in the shared tree (which
 * 1.21.1 still discovers by annotation) are wired up here instead: each becomes a DirectGameTest
 * registered from NeoForge's RegisterGameTestsEvent. The test bodies are untouched -- a test is
 * still a method taking a GameTestHelper -- so this file is purely the wiring the annotations
 * used to provide.
 *
 * <p>Deliberately NOT the TEST_FUNCTION registry that 26.x's own tests use: that registry is
 * built during bootstrap, before any mod can add to it. See DirectGameTest.
 */
@EventBusSubscriber(modid = {cls}.MODID{d['bus']})
public final class {cls} {{
    public static final String MODID = "{modid}";
    public static final int COUNT = {len(tests)};

    private {cls}() {{}}

    private static {d['ident']} id(String name) {{
        return {d['ident']}.fromNamespaceAndPath(MODID, name);
    }}

    /**
     * Register the instance TYPE's codec.
     *
     * <p>This lives here, on a listener FML subscribes for us, rather than in a DeferredRegister
     * the mod constructor has to remember to attach: nothing type-checks "somebody calls this",
     * and when nobody did, every gate stayed green while a real client could not join a world at
     * all -- TEST_INSTANCE is synced, so the known-packs handshake threw
     * "Unregistered holder in ResourceKey[minecraft:root / minecraft:test_instance_type]" and the
     * player sat on the loading screen forever. A registration that cannot be forgotten is worth
     * more than one that is merely documented.
     */
    @SubscribeEvent
    static void onRegisterTestInstanceType(RegisterEvent e) {{
        e.register(Registries.TEST_INSTANCE_TYPE, id("direct"), () -> DirectGameTest.CODEC);
    }}

    @SubscribeEvent
    static void onRegisterTests(RegisterGameTestsEvent e) {{
        // An empty AllOf is the do-nothing environment: these tests set up their own world
        // state, exactly as they did under the annotation model.
        Holder<{d['envt']}> env =
                e.registerEnvironment(id("default"), new TestEnvironmentDefinition.AllOf(List.of()));
{chr(10).join(insts)}
    }}
}}
'''
    with open(os.path.join(pkg_dir, cls + '.java'), 'w', encoding='utf-8') as f:
        f.write(body)


def self_check():
    import tempfile
    ok = True
    src = ('package p.t;\nimport net.minecraft.gametest.framework.GameTest;\nimport net.minecraft.gametest.framework.GameTestHelper;\n'
           'import net.neoforged.neoforge.gametest.GameTestHolder;\n@GameTestHolder("m")\npublic class A {\n'
           '    @GameTest(template = "empty_test", timeoutTicks = 50)\n    public static void fooBar(GameTestHelper h) {}\n'
           '    @GameTest\n    public void baz(GameTestHelper h) {}\n}\n')
    for dialect, ident, bus in (('26', 'Identifier', False), ('1.21.5', 'ResourceLocation', True),
                              ('1.21.8', 'ResourceLocation', False), ('1.21.11', 'Identifier', False)):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'p/t'))
            open(os.path.join(d, 'p/t/A.java'), 'w', encoding='utf-8').write(src)
            ts = adapt(d, 'm', 'p.t', 'empty_test', dialect=dialect)
            a = open(os.path.join(d, 'p/t/A.java'), encoding='utf-8').read()
            reg = open(os.path.join(d, 'p/t/GeneratedGameTests.java'), encoding='utf-8').read()
            dg = open(os.path.join(d, 'p/t/DirectGameTest.java'), encoding='utf-8').read()
            good = (len(ts) == 2 and '@GameTest' not in a and 'GameTestHolder' not in a
                    and 'GameTestHelper;' in a and 'a/foo_bar' in reg and 'a/baz' in reg
                    and ('bus = EventBusSubscriber.Bus.MOD' in reg) == bus
                    and f'import net.minecraft.resources.{ident};' in reg and f'import net.minecraft.resources.{ident};' in dg
                    and ('TestEnvironmentDefinition<?>' in reg) == (dialect == '26')
                    and ('Identifier' not in reg) == (dialect not in ('26', '1.21.11')))
            if not good:
                ok = False
                print(f"FAIL: gametest adapter dialect {dialect}")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    import argparse
    if '--self-check' in sys.argv:
        sys.exit(self_check())
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--modid', required=True)
    ap.add_argument('--package', default=None,
                    help='Package for the generated registrar (default: <modid>.compat)')
    ap.add_argument('--cls', default='GeneratedGameTests',
                    help='Class name for the generated registrar')
    ap.add_argument('--structure', default='empty_test')
    ap.add_argument('--dialect', choices=sorted(DIALECTS), default='26',
                    help="'26' (26.x: Identifier, generic environment), '1.21.5' (ResourceLocation, FML bus attribute) "
                         "'1.21.8' (1.21.6+: ResourceLocation, no bus attribute) "
                         "or '1.21.11' (Identifier, non-generic environment, no bus attribute)")
    a = ap.parse_args()
    if not a.package:
        a.package = a.modid + '.compat'
    ts = adapt(a.root, a.modid, a.package, a.structure, cls=a.cls, dialect=a.dialect)
    print(f"gametest-adapter: wired {len(ts)} tests "
          f"({sum(1 for t in ts if not t['static'])} non-static) -> {a.package}.{a.cls}")
