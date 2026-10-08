#!/usr/bin/env python3
"""Convert a Forge SimpleChannel to NeoForge 1.21.1 payloads, keeping the mod's own structure.

    python3 tools/convert-simplechannel.py --src src/main/java --modid <modid> [--dry-run] [--self-check]

Forge 1.20 networking (CATALOG §E15-19) is the same few shapes in nearly every mod, which is why it is
worth a generator rather than a worker: a channel class holding `SimpleChannel CHANNEL` and a register()
of `messageBuilder(P.class, id, dir).encoder(E).decoder(D).consumerMainThread(H).add()` chains, and
packet classes with `encode(P, buf)` / `decode(buf)` / `handle(P, Supplier<NetworkEvent.Context>)`.
The conversion keeps every one of those methods -- the codec is built FROM them -- so the diff is the
wiring, not the packet logic:

  channel  register() becomes register(RegisterPayloadHandlersEvent) with one registrar line per chain
           (playToClient / playToServer / playBidirectional; consumerNetworkThread keeps its thread);
           the call site becomes `<modBus>.addListener(Channel::register)`; CHANNEL.send(...) calls become
           PacketDistributor.sendTo...(...) wherever they are, and the CHANNEL field goes.
  packet   `implements CustomPacketPayload`, plus TYPE, STREAM_CODEC (from the existing encode/decode) and
           type(); handle(..) takes IPayloadContext: ctx.get().x() -> ctx.x(), setPacketHandled dropped,
           getSender() -> (ServerPlayer) ctx.player().

A registration whose direction is not stated is given one from how the packet is SENT (sendToServer ->
serverbound, a PacketDistributor target -> clientbound), and is bidirectional only when it is sent both
ways or never. Anything it cannot convert completely it lists by name and leaves alone.
Standard library only.
"""
import argparse, collections, importlib.util, pathlib, re, sys

_spec = importlib.util.spec_from_file_location("forge_shapes", pathlib.Path(__file__).resolve().parent / "forge-shapes.py")
fs = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fs)
match, split_args, methods, add_import, remove_import_if_unused = (fs.match, fs.split_args, fs.methods, fs.add_import,
                                                                   fs.remove_import_if_unused)

CPP = "net.minecraft.network.protocol.common.custom.CustomPacketPayload"
IMPORTS_PACKET = [CPP, "net.minecraft.network.codec.StreamCodec", "net.minecraft.network.RegistryFriendlyByteBuf",
                  "net.minecraft.resources.ResourceLocation", "net.neoforged.neoforge.network.handling.IPayloadContext"]
DEAD = ["net.neoforged.neoforge.network.NetworkEvent", "net.minecraftforge.network.NetworkEvent",
        "net.neoforged.neoforge.network.simple.SimpleChannel", "net.minecraftforge.network.simple.SimpleChannel",
        "net.neoforged.neoforge.network.NetworkRegistry", "net.minecraftforge.network.NetworkRegistry",
        "net.neoforged.neoforge.network.NetworkDirection", "net.minecraftforge.network.NetworkDirection",
        "java.util.function.Supplier", "net.minecraftforge.network.PacketDistributor"]
TARGETS = {"PLAYER": "sendToPlayer", "TRACKING_ENTITY": "sendToPlayersTrackingEntity",
           "TRACKING_ENTITY_AND_SELF": "sendToPlayersTrackingEntityAndSelf"}


def snake(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


class Chain:
    __slots__ = ("start", "end", "cls", "direction", "enc", "dec", "handler", "network_thread")


def chains(text, chan):
    """messageBuilder(...)...add(); and registerMessage(...) registrations on `chan`."""
    out = []
    for m in re.finditer(r"(?<![\w$])(?:[\w.]+\.)?%s\s*\.\s*messageBuilder\(" % re.escape(chan), text):
        p0 = m.end() - 1; p1 = match(text, p0)
        args = [a.strip() for a in split_args(text[p0 + 1:p1])]
        end = text.find(".add()", p1)
        semi = text.find(";", end)
        if end < 0 or semi < 0 or not args or not args[0].endswith(".class"):
            continue
        tail = text[p1 + 1:end]
        c = Chain()
        c.start, c.end, c.cls = m.start(), semi + 1, args[0][:-6]
        c.direction = next((d for d in ("PLAY_TO_CLIENT", "PLAY_TO_SERVER") if any(d in a for a in args[2:])), None)
        g = lambda k: (re.search(r"\.%s\(\s*([^()]+?)\s*\)" % k, tail) or [None, None])[1]
        c.enc, c.dec = g("encoder"), g("decoder")
        c.handler = g("consumerMainThread") or g("consumerNetworkThread") or g("consumer")
        c.network_thread = bool(g("consumerNetworkThread"))
        if c.enc and c.dec and c.handler:
            out.append(c)
    for m in re.finditer(r"(?<![\w$])(?:[\w.]+\.)?%s\s*\.\s*registerMessage\(" % re.escape(chan), text):
        p0 = m.end() - 1; p1 = match(text, p0)
        args = [a.strip() for a in split_args(text[p0 + 1:p1])]
        semi = text.find(";", p1)
        if len(args) < 5 or not args[1].endswith(".class"):
            continue
        c = Chain()
        c.start, c.end, c.cls = m.start(), semi + 1, args[1][:-6]
        c.enc, c.dec, c.handler = args[2], args[3], args[4]
        c.direction = next((d for d in ("PLAY_TO_CLIENT", "PLAY_TO_SERVER") if len(args) > 5 and d in args[5]), None)
        c.network_thread = False
        out.append(c)
    return out


def infer_direction(cls, files):
    to_server = to_client = False
    for t in files.values():
        for m in re.finditer(r"new\s+%s\s*\(" % re.escape(cls), t):
            ln_s = t.rfind(";", 0, m.start()); ln_e = t.find(";", m.end())
            stmt = t[ln_s + 1:ln_e]
            if re.search(r"sendToServer\s*\(", stmt):
                to_server = True
            elif re.search(r"\bsend\w*\s*\(|PacketDistributor", stmt):
                to_client = True
    if to_server and not to_client:
        return "PLAY_TO_SERVER"
    if to_client and not to_server:
        return "PLAY_TO_CLIENT"
    return None


def class_file(cls, files, near):
    simple = cls.rsplit(".", 1)[-1]
    hits = [f for f in files if f.stem == simple]
    if len(hits) > 1:
        hits = sorted(hits, key=lambda f: -len(set(f.parts) & set(near.parts)))
    return hits[0] if hits else None


def rewrite_sends(text, chan, flags, rel):
    """CHANNEL.send(...) / sendToServer / sendTo -> PacketDistributor statics."""
    out, i, n = [], 0, 0
    for m in re.finditer(r"(?<![\w$])(?:[\w.]+\.)?%s\s*\.\s*(send|sendToServer|sendTo)\(" % re.escape(chan), text):
        if m.start() < i:
            continue
        p0 = m.end() - 1; p1 = match(text, p0)
        args = [a.strip() for a in split_args(text[p0 + 1:p1])]
        rep = None
        if m.group(1) == "sendToServer" and len(args) == 1:
            rep = f"PacketDistributor.sendToServer({args[0]})"
        elif m.group(1) == "send" and len(args) == 2:
            t = re.fullmatch(r"PacketDistributor\.(\w+)\.(with|noArg)\((.*)\)", args[0], re.S)
            if t and t.group(1) in TARGETS:
                target = re.sub(r"^\(\)\s*->\s*", "", t.group(3).strip())
                rep = f"PacketDistributor.{TARGETS[t.group(1)]}({target}, {args[1]})"
            elif t and t.group(1) == "DIMENSION" and re.fullmatch(r"\(\)\s*->\s*([\w.]+)\.dimension\(\)", t.group(3).strip()):
                lvl = re.fullmatch(r"\(\)\s*->\s*([\w.]+)\.dimension\(\)", t.group(3).strip()).group(1)
                rep = f"PacketDistributor.sendToPlayersInDimension({lvl}, {args[1]})"
            elif t and t.group(1) == "ALL":
                rep = f"PacketDistributor.sendToAllPlayers({args[1]})"
        elif m.group(1) == "sendTo" and len(args) == 3:
            pm = re.fullmatch(r"([\w.()]+?)\.connection\.(?:connection|getConnection\(\))", args[1])
            if pm:
                rep = f"PacketDistributor.sendToPlayer({pm.group(1)}, {args[0]})"
        if rep is None:
            flags.append(f"{rel}:{fs.line_of(text, m.start())} {chan}.{m.group(1)}(...) target not convertible")
            continue
        out.append(text[i:m.start()] + rep); i = p1 + 1; n += 1
    out.append(text[i:])
    return "".join(out), n


def convert_packet(text, cls, enc, dec, modid, used_ids, flags, rel):
    """Add CustomPacketPayload + TYPE + STREAM_CODEC + type() to class `cls` in text."""
    simple = cls.rsplit(".", 1)[-1]
    if re.search(r"\bType<%s>\s+TYPE\b" % re.escape(simple), text):
        return text, False
    m = re.search(r"(?m)^[ \t]*(?:public\s+|final\s+|static\s+|abstract\s+)*(class|record)\s+%s\b" % re.escape(simple), text)
    if not m:
        flags.append(f"{rel}: class {simple} not found"); return text, False
    if m.group(1) == "record":
        rp = text.find("(", m.end()); rp_end = match(text, rp)
        head_end = rp_end + 1
    else:
        head_end = m.end()
    brace = text.find("{", head_end)
    header = text[head_end:brace]
    if re.search(r"\bimplements\b", header):
        header2 = re.sub(r"\bimplements\b", "implements CustomPacketPayload,", header, 1)
    else:
        header2 = header.rstrip() + " implements CustomPacketPayload "
    # codec from the registration's own encoder/decoder references
    em = re.fullmatch(r"(?:[\w.]+\.)?(\w+)::(\w+)", enc.strip())
    dm = re.fullmatch(r"(?:[\w.]+\.)?(\w+)::(\w+)", dec.strip())
    if not em or not dm:
        flags.append(f"{rel}: {simple} encoder/decoder are not method references"); return text, False
    enc_m = next((x for x in methods(text) if x.name == em.group(2)), None)
    if enc_m is None:
        flags.append(f"{rel}: {simple}.{em.group(2)} not found"); return text, False
    is_static_enc = len(enc_m.params) == 2
    encoder = (f"(buf, msg) -> {em.group(1)}.{em.group(2)}(msg, buf)" if is_static_enc
               else f"(buf, msg) -> msg.{em.group(2)}(buf)")
    decoder = f"{dm.group(1)}::{dm.group(2)}"
    path = snake(simple)
    k = 2
    while path in used_ids:
        path = f"{snake(simple)}_{k}"; k += 1
    used_ids.add(path)
    ind = "    "
    members = (f"\n{ind}public static final CustomPacketPayload.Type<{simple}> TYPE =\n"
               f"{ind}        new CustomPacketPayload.Type<>(ResourceLocation.fromNamespaceAndPath(\"{modid}\", \"{path}\"));\n"
               f"{ind}public static final StreamCodec<RegistryFriendlyByteBuf, {simple}> STREAM_CODEC =\n"
               f"{ind}        StreamCodec.of({encoder}, {decoder});\n\n"
               f"{ind}@Override\n{ind}public CustomPacketPayload.Type<{simple}> type() {{\n{ind}    return TYPE;\n{ind}}}\n")
    text = text[:head_end] + header2 + text[brace:brace + 1] + members + text[brace + 1:]
    return text, True


def convert_handler(text, hname, flags, rel):
    """handle(P, Supplier<NetworkEvent.Context> ctx) -> handle(P, IPayloadContext ctx)."""
    n = 0
    for mt in sorted(methods(text), key=lambda x: -x.start):
        if mt.name != hname or not mt.params or not re.fullmatch(r"Supplier<\s*NetworkEvent\.Context\s*>", fs.ptype(mt.params[-1])):
            continue
        cs = fs.pname(mt.params[-1])
        body = text[mt.body_open:mt.body_close + 1]
        alias = re.search(r"\n[ \t]*NetworkEvent\.Context\s+(\w+)\s*=\s*%s\.get\(\)\s*;[ \t]*" % re.escape(cs), body)
        name = cs
        if alias:
            name = alias.group(1)
            body = body[:alias.start()] + body[alias.end():]
        body = re.sub(r"(?<![\w$.])%s\.get\(\)" % re.escape(cs), name, body)
        body = re.sub(r"\n[ \t]*%s\.setPacketHandled\(\s*true\s*\)\s*;[ \t]*" % re.escape(name), "", body)
        body = re.sub(r"(?<![\w$.])%s\.getSender\(\)" % re.escape(name), f"((ServerPlayer) {name}.player())", body)
        body = re.sub(r"(?<![\w$.])%s\.getDirection\(\)" % re.escape(name), f"{name}.flow()", body)
        body = re.sub(r"\bNetworkDirection\.PLAY_TO_CLIENT\b", "PacketFlow.CLIENTBOUND", body)
        body = re.sub(r"\bNetworkDirection\.PLAY_TO_SERVER\b", "PacketFlow.SERVERBOUND", body)
        if re.search(r"(?<![\w$.])%s\.(?!enqueueWork|player|flow|reply|disconnect|channelHandlerContext|connection|handle|listener|protocol)\w+\(" % re.escape(name), body) \
                or "NetworkDirection" in body or "returns" == "x":
            flags.append(f"{rel}:{fs.line_of(text, mt.start)} {hname}: context use not converted")
            continue
        params = mt.params[:-1] + [f"IPayloadContext {name}"]
        text = text[:mt.params_open + 1] + ", ".join(params) + text[mt.params_close:mt.body_open] + body + text[mt.body_close + 1:]
        if "PacketFlow." in body:
            text = add_import(text, "net.minecraft.network.protocol.PacketFlow")
        if "ServerPlayer" in body:
            text = add_import(text, "net.minecraft.server.level.ServerPlayer")
        n += 1
    return text, n


def buses_in_scope(text, pos):
    """Names of IEventBus parameters/locals visible at pos (a constructor's included)."""
    out = []
    for m in re.finditer(r"\bIEventBus\s+(\w+)\s*([,)=;])", text[:pos]):
        if m.group(2) in ",)":                        # a parameter: its method body must contain pos
            b = text.find("{", m.end())
            if b >= 0 and b < pos and match(text, b) > pos:
                out.append(m.group(1))
        else:                                          # a local: its enclosing block must contain pos
            depth, j = 0, m.start()
            while j > 0:
                j -= 1
                if text[j] == "}":
                    depth += 1
                elif text[j] == "{":
                    if depth == 0:
                        break
                    depth -= 1
            if text[j] == "{" and match(text, j) > pos:
                out.append(m.group(1))
    return out


def run(src, modid, dry=False):
    src = pathlib.Path(src).resolve()
    files = {f: f.read_text(encoding="utf-8") for f in src.rglob("*.java")}
    new = dict(files)
    flags, report = [], collections.Counter()
    used_ids = set()
    for f, t in files.items():
        cm = re.search(r"(?:public\s+)?(?:static\s+)?(?:final\s+)?SimpleChannel\s+(\w+)\s*=\s*(NetworkRegistry\.newSimpleChannel\(|ChannelBuilder)", t)
        if not cm:
            continue
        chan = cm.group(1)
        rel = f.relative_to(src).as_posix()
        p0 = t.find("(", cm.end() - 1) if cm.group(2).startswith("NetworkRegistry") else -1
        version = '"1"'
        if p0 > 0:
            va = split_args(t[p0 + 1:match(t, p0)])
            vm = re.fullmatch(r"\(\)\s*->\s*(.+)", va[1].strip()) if len(va) > 1 else None
            if vm:
                version = vm.group(1).strip()
        regs = chains(t, chan)
        if not regs:
            flags.append(f"{rel}: channel {chan} has no registration this converts"); continue
        ms = methods(t)
        owner = next((x for x in ms if x.body_open < regs[0].start < x.body_close), None)
        if owner is None or any(not (owner.body_open < c.start < owner.body_close) for c in regs):
            flags.append(f"{rel}: registrations are not all in one method"); continue
        # 1. packets
        packets = {}
        for c in regs:
            pf = class_file(c.cls, files, f)
            if pf is None:
                flags.append(f"{rel}: packet class {c.cls} not in the source"); continue
            txt = new[pf]
            txt, ok = convert_packet(txt, c.cls, c.enc, c.dec, modid, used_ids, flags, pf.relative_to(src).as_posix())
            hm = re.fullmatch(r"(?:([\w.]+)\.)?(\w+)::(\w+)", c.handler.strip())
            if hm:
                hf = pf if hm.group(2) == c.cls.rsplit(".", 1)[-1] else class_file(hm.group(2), files, f)
                if hf == pf:
                    txt, k = convert_handler(txt, hm.group(3), flags, pf.relative_to(src).as_posix())
                else:
                    new[pf] = txt
                    t2, k = convert_handler(new[hf], hm.group(3), flags, hf.relative_to(src).as_posix())
                    new[hf] = t2; txt = new[pf]
                report["handlers"] += k
            if ok:
                for imp in IMPORTS_PACKET:
                    txt = add_import(txt, imp)
                report["packets"] += 1
            new[pf] = txt
            packets[c.cls] = c
        # 2. the register() method
        t = new[f]
        lines = []
        for c in regs:
            d = c.direction or infer_direction(c.cls.rsplit(".", 1)[-1], files)
            fn = {"PLAY_TO_CLIENT": "playToClient", "PLAY_TO_SERVER": "playToServer"}.get(d, "playBidirectional")
            reg = "registrar.executesOn(HandlerThread.NETWORK)" if c.network_thread else "registrar"
            lines.append(f"{reg}.{fn}({c.cls}.TYPE, {c.cls}.STREAM_CODEC, {c.handler.strip()});")
            report[fn] += 1
        ms = methods(t)
        owner = next(x for x in ms if x.body_open < regs[0].start < x.body_close)
        ind = re.match(r"[ \t]*", t[t.rfind("\n", 0, regs[0].start) + 1:]).group(0)
        first = regs[0].start - (regs[0].start - (t.rfind("\n", 0, regs[0].start) + 1))
        body_pieces, cur = [], owner.body_open + 1
        # replace each chain with its registrar line, keep everything between them (comments, blank lines)
        for c, line in zip(regs, lines):
            body_pieces.append(t[cur:c.start]); body_pieces.append(line); cur = c.end
        body_pieces.append(t[cur:owner.body_close])
        body = "".join(body_pieces)
        body = f"\n{ind}PayloadRegistrar registrar = event.registrar({version});" + body
        t = (t[:owner.params_open + 1] + "RegisterPayloadHandlersEvent event" + t[owner.params_close:owner.body_open + 1]
             + body + t[owner.body_close:])
        # 3. the channel field
        cm2 = re.search(r"(?m)^[ \t]*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:final\s+)?SimpleChannel\s+%s\s*=" % re.escape(chan), t)
        e = cm2.end()
        while True:
            k = fs._skip(t, e) if e < len(t) else e
            if k != e:
                e = k; continue
            if t[e] in "({[":
                e = match(t, e) + 1; continue
            if t[e] == ";":
                break
            e += 1
        t = t[:cm2.start()] + t[e + 2 if t[e + 1:e + 2] == "\n" else e + 1:]
        for imp in ("net.neoforged.neoforge.network.event.RegisterPayloadHandlersEvent",
                    "net.neoforged.neoforge.network.registration.PayloadRegistrar"):
            t = add_import(t, imp)
        if "HandlerThread." in t:
            t = add_import(t, "net.neoforged.neoforge.network.registration.HandlerThread")
        new[f] = t
        # 4. sends, everywhere
        for g in list(new):
            t2, k = rewrite_sends(new[g], chan, flags, g.relative_to(src).as_posix())
            if k:
                t2 = add_import(t2, "net.neoforged.neoforge.network.PacketDistributor")
                t2 = re.sub(r"(public\s+static\s+)<(\w+)>(\s+void\s+\w+\(\s*\2\s+\w+)", r"\1<\2 extends CustomPacketPayload>\3", t2)
                if "CustomPacketPayload" in t2:
                    t2 = add_import(t2, CPP)
                new[g] = t2; report["sends"] += k
        # 5. the call site of register()
        cname = f.stem
        for g in list(new):
            for m in re.finditer(r"(?<![\w$.])%s\.%s\(\s*\);" % (re.escape(cname), owner.name), new[g]):
                bus = buses_in_scope(new[g], m.start())
                if bus:
                    new[g] = new[g][:m.start()] + f"{bus[-1]}.addListener({cname}::{owner.name});" + new[g][m.end():]
                    report["call sites"] += 1
                else:
                    flags.append(f"{g.relative_to(src).as_posix()}:{fs.line_of(new[g], m.start())} {cname}.{owner.name}() "
                                 "has no IEventBus in scope: add `modBus.addListener(" + cname + "::" + owner.name + ")`")
                break
    changed = 0
    for f, t in new.items():
        if t != files[f]:
            for d in DEAD:
                t = remove_import_if_unused(t, d)
            changed += 1
            if not dry:
                f.write_text(t, encoding="utf-8")
    return report, flags, changed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--modid"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sites", type=int, default=8); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.modid):
        ap.error("--src and --modid are required")
    report, flags, changed = run(a.src, a.modid, a.dry_run)
    print(f"convert-simplechannel: {changed} file(s) changed" + (" (dry run)" if a.dry_run else "")
          + "".join(f", {v} {k}" for k, v in sorted(report.items())))
    if not report:
        print("  no SimpleChannel found (converted nothing)")
    for s in flags[:a.sites]:
        print("  left by name: " + s)
    if len(flags) > a.sites:
        print(f"  ... and {len(flags) - a.sites} more")
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d) / "my" / "net"
        r.mkdir(parents=True)
        (r / "Net.java").write_text("""package my.net;

import net.neoforged.neoforge.network.NetworkDirection;
import net.neoforged.neoforge.network.NetworkRegistry;
import net.neoforged.neoforge.network.PacketDistributor;
import net.neoforged.neoforge.network.simple.SimpleChannel;

public class Net {
    private static final String PROTOCOL_VERSION = "3";
    public static final SimpleChannel CHANNEL = NetworkRegistry.newSimpleChannel(
            ResourceLocation.fromNamespaceAndPath("mymod", "main"),
            () -> PROTOCOL_VERSION,
            PROTOCOL_VERSION::equals,
            PROTOCOL_VERSION::equals
    );
    private static int id = 0;

    public static void register() {
        // to the client
        CHANNEL.messageBuilder(PingPacket.class, id++, NetworkDirection.PLAY_TO_CLIENT)
                .encoder(PingPacket::encode)
                .decoder(PingPacket::decode)
                .consumerMainThread(PingPacket::handle)
                .add();
        CHANNEL.messageBuilder(PongPacket.class, id++)
                .encoder(PongPacket::encode)
                .decoder(PongPacket::decode)
                .consumerNetworkThread(PongPacket::handle)
                .add();
    }

    public static <MSG> void sendToServer(MSG message) {
        CHANNEL.sendToServer(message);
    }

    public static <MSG> void sendToPlayer(MSG message, ServerPlayer player) {
        CHANNEL.send(PacketDistributor.PLAYER.with(() -> player), message);
    }

    public static <MSG> void sendToDimension(MSG message, ServerLevel level) {
        CHANNEL.send(
                PacketDistributor.DIMENSION.with(() -> level.dimension()),
                message
        );
    }
}
""", encoding="utf-8")
        (r / "PingPacket.java").write_text("""package my.net;

import java.util.function.Supplier;
import net.neoforged.neoforge.network.NetworkEvent;

public class PingPacket {
    public final int n;
    public PingPacket(int n) { this.n = n; }
    public static void encode(PingPacket msg, FriendlyByteBuf buf) { buf.writeInt(msg.n); }
    public static PingPacket decode(FriendlyByteBuf buf) { return new PingPacket(buf.readInt()); }
    public static void handle(PingPacket msg, Supplier<NetworkEvent.Context> ctx) {
        ctx.get().enqueueWork(() -> {
            if (ctx.get().getDirection() != NetworkDirection.PLAY_TO_CLIENT) return;
            Client.ping(msg.n);
        });
        ctx.get().setPacketHandled(true);
    }
}
""", encoding="utf-8")
        (r / "PongPacket.java").write_text("""package my.net;

import java.util.function.Supplier;
import net.neoforged.neoforge.network.NetworkEvent;

public record PongPacket(int n) {
    public static void encode(PongPacket msg, FriendlyByteBuf buf) { buf.writeInt(msg.n); }
    public static PongPacket decode(FriendlyByteBuf buf) { return new PongPacket(buf.readInt()); }
    public static void handle(PongPacket msg, Supplier<NetworkEvent.Context> contextSupplier) {
        NetworkEvent.Context context = contextSupplier.get();
        context.enqueueWork(() -> Server.pong(context.getSender(), msg.n));
        context.setPacketHandled(true);
    }
}
""", encoding="utf-8")
        (r / "Use.java").write_text("""package my.net;
class Use { void a() { Net.sendToServer(new PongPacket(1)); } }
""", encoding="utf-8")
        (r / "Mod.java").write_text("""package my.net;
class Mod { public Mod(IEventBus modEventBus) { Net.register(); } }
""", encoding="utf-8")
        report, flags, _ = run(d, "mymod")
        net = (r / "Net.java").read_text(); ping = (r / "PingPacket.java").read_text(); pong = (r / "PongPacket.java").read_text()
        mod = (r / "Mod.java").read_text()
        want = [(net, "public static void register(RegisterPayloadHandlersEvent event) {"),
                (net, "PayloadRegistrar registrar = event.registrar(PROTOCOL_VERSION);"),
                (net, "// to the client"),
                (net, "registrar.playToClient(PingPacket.TYPE, PingPacket.STREAM_CODEC, PingPacket::handle);"),
                (net, "registrar.executesOn(HandlerThread.NETWORK).playToServer(PongPacket.TYPE, PongPacket.STREAM_CODEC, PongPacket::handle);"),
                (net, "public static <MSG extends CustomPacketPayload> void sendToServer(MSG message) {"),
                (net, "PacketDistributor.sendToServer(message);"),
                (net, "PacketDistributor.sendToPlayer(player, message);"),
                (ping, "public class PingPacket implements CustomPacketPayload {"),
                (ping, 'ResourceLocation.fromNamespaceAndPath("mymod", "ping_packet")'),
                (ping, "StreamCodec.of((buf, msg) -> PingPacket.encode(msg, buf), PingPacket::decode);"),
                (ping, "public static void handle(PingPacket msg, IPayloadContext ctx) {"),
                (ping, "if (ctx.flow() != PacketFlow.CLIENTBOUND) return;"),
                (net, "PacketDistributor.sendToPlayersInDimension(level, message)"),
                (pong, "public record PongPacket(int n) implements CustomPacketPayload {"),
                (pong, "public static void handle(PongPacket msg, IPayloadContext context) {"),
                (pong, "Server.pong(((ServerPlayer) context.player()), msg.n)"),
                (mod, "modEventBus.addListener(Net::register);")]
        miss = [w for t, w in want if w not in t]
        for bad, t in (("SimpleChannel", net), ("NetworkEvent", ping), ("setPacketHandled", pong), ("Supplier", pong),
                       ("CHANNEL", net)):
            if bad in t:
                miss.append(f"still has {bad}")
        before = {p: p.read_text() for p in r.iterdir()}
        run(d, "mymod")
        if any(p.read_text() != before[p] for p in r.iterdir()):
            miss.append("not idempotent")
        ok = not miss and not flags
    print("self-check:", "OK" if ok else f"FAIL {miss} {flags}\n{net}\n{pong}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
