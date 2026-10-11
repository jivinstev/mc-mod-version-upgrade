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
        "java.util.function.Supplier", "net.minecraftforge.network.PacketDistributor",
        # Forge 1.20.2+ networking (ChannelBuilder / CustomPayloadEvent), as written and as the import codemod renamed it
        "net.minecraftforge.event.network.CustomPayloadEvent", "net.neoforged.neoforge.event.network.CustomPayloadEvent",
        "net.minecraftforge.event.network.CustomPayloadEvent.Context",
        "net.minecraftforge.network.NetworkEvent.Context", "net.neoforged.neoforge.network.NetworkEvent.Context",
        "net.neoforged.neoforge.event.network.CustomPayloadEvent.Context",
        "net.minecraftforge.network.SimpleChannel", "net.neoforged.neoforge.network.SimpleChannel",
        "net.minecraftforge.network.ChannelBuilder", "net.neoforged.neoforge.network.ChannelBuilder",
        "net.minecraftforge.network.Channel", "net.neoforged.neoforge.network.Channel",
        # X120: Forge 1.20.1's builder is NESTED in NetworkRegistry, and Vineflower imports it that way
        "net.minecraftforge.network.NetworkRegistry.ChannelBuilder", "net.neoforged.neoforge.network.NetworkRegistry.ChannelBuilder"]
TARGETS = {"PLAYER": "sendToPlayer", "TRACKING_ENTITY": "sendToPlayersTrackingEntity",
           "TRACKING_ENTITY_AND_SELF": "sendToPlayersTrackingEntityAndSelf"}


def snake(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


class Chain:
    __slots__ = ("start", "end", "cls", "direction", "enc", "dec", "handler", "network_thread")


# `(pkt, sup) -> { Context ctx = (Context)sup.get(); ctx.enqueueWork(() -> P.handle(pkt, ctx)); ctx.setPacketHandled(true); }`
# -- a wrapper that only moves the call to the main thread, which is where a NeoForge payload handler already runs:
# it is `P::handle` (X104). Anything else in the lambda leaves it alone.
WRAPPER = re.compile(r"\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*->\s*\{\s*(?:(?:final\s+)?(?:[\w.]*Context)\s+)?(\w+)\s*=\s*"
                     r"(?:\(\s*[\w.]*Context\s*\)\s*)?\2\s*\.\s*get\(\)\s*;\s*\3\s*\.\s*enqueueWork\(\s*\(\)\s*->\s*"
                     r"\{?\s*([\w.]+)\s*\(\s*\1\s*,\s*\3\s*\)\s*;?\s*\}?\s*\)\s*;\s*(?:\3\s*\.\s*setPacketHandled\(\s*true\s*\)\s*;\s*)?\}",
                     re.S)


def unwrap_handler(h):
    m = WRAPPER.fullmatch(h.strip())
    if not m:
        return h
    q = m.group(4)
    return q[::-1].replace(".", "::", 1)[::-1] if "." in q else h


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
        c.handler = unwrap_handler(g("consumerMainThread") or g("consumerNetworkThread") or g("consumer") or "")
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
        c.enc, c.dec, c.handler = args[2], args[3], unwrap_handler(args[4])
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
    if not hits and isinstance(files, dict):     # a NESTED message class (MCreator's <Mod>Variables.SavedDataSyncMessage)
        hits = [f for f, t in files.items() if re.search(r"\b(?:class|record)\s+%s\b" % re.escape(simple), t)]
    if len(hits) > 1:
        hits = sorted(hits, key=lambda f: -len(set(f.parts) & set(near.parts)))
    return hits[0] if hits else None


# `() -> level.dimension()`, MCreator's `() -> ((Level)level).dimension()`, and Vineflower's `level::dimension`
DIM = r"(?:\(\)\s*->\s*(?:\(\(\w+\)\s*)?([\w.]+)\)?\.dimension\(\)|([\w.]+)::dimension)"


def rewrite_sends(text, chan, flags, rel):
    """CHANNEL.send(...) / sendToServer / sendTo -> PacketDistributor statics."""
    out, i, n = [], 0, 0
    for m in re.finditer(r"(?<![\w$])(?:[\w.]+\.)?%s\s*\.\s*(send|sendToServer|sendTo)\(" % re.escape(chan), text):
        if m.start() < i:
            continue
        p0 = m.end() - 1; p1 = match(text, p0)
        args = [re.sub(r"^\(Object\)\s*", "", a.strip()) for a in split_args(text[p0 + 1:p1])]   # MCreator casts the message
        rep = None
        if m.group(1) == "sendToServer" and len(args) == 1:
            rep = f"PacketDistributor.sendToServer({args[0]})"
        elif m.group(1) == "send" and len(args) == 2:
            # Forge 1.20.2+ reversed the order to send(message, target); a target is recognisable, a message is not
            tgt = r"PacketDistributor\.\w+\.(?:with|noArg)\(.*\)|[\w.()]+?\.connection\.(?:connection|getConnection\(\))"
            if re.fullmatch(tgt, args[1], re.S) and not re.fullmatch(tgt, args[0], re.S):
                args = [args[1], args[0]]
            pm = re.fullmatch(r"([\w.()]+?)\.connection\.(?:connection|getConnection\(\))", args[0])
            t = re.fullmatch(r"PacketDistributor\.(\w+)\.(with|noArg)\((.*)\)", args[0], re.S)
            if pm:
                rep = f"PacketDistributor.sendToPlayer({pm.group(1)}, {args[1]})"
            elif t and t.group(1) == "SERVER":
                rep = f"PacketDistributor.sendToServer({args[1]})"
            elif t and t.group(1) in TARGETS:
                target = re.sub(r"^\(\)\s*->\s*", "", t.group(3).strip())
                rep = f"PacketDistributor.{TARGETS[t.group(1)]}({target}, {args[1]})"
            elif t and t.group(1) == "DIMENSION" and re.fullmatch(DIM, t.group(3).strip()):
                dm_ = re.fullmatch(DIM, t.group(3).strip()); lvl = dm_.group(1) or dm_.group(2)
                rep = f"PacketDistributor.sendToPlayersInDimension({lvl}, {args[1]})"
            elif t and t.group(1) == "DIMENSION" and re.fullmatch(r"\(\)\s*->\s*([\w.]+)", t.group(3).strip()):
                # a bare ResourceKey<Level> (X154): the server's level for it
                key = re.fullmatch(r"\(\)\s*->\s*([\w.]+)", t.group(3).strip()).group(1)
                rep = (f"PacketDistributor.sendToPlayersInDimension(net.neoforged.neoforge.server.ServerLifecycleHooks"
                       f".getCurrentServer().getLevel({key}), {args[1]})")
            elif t and t.group(1) == "TRACKING_CHUNK" and re.fullmatch(r"\(\)\s*->\s*(.+)", t.group(3).strip(), re.S):
                ch = re.fullmatch(r"\(\)\s*->\s*(.+)", t.group(3).strip(), re.S).group(1).strip()
                rep = (f"PacketDistributor.sendToPlayersTrackingChunk((net.minecraft.server.level.ServerLevel) ({ch}).getLevel(), "
                       f"({ch}).getPos(), {args[1]})")
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
        if mt.name != hname or not mt.params:
            continue
        pt = fs.ptype(mt.params[-1])
        # Forge 1.20.2+: no Supplier. Vineflower writes it bare (`handle(Context ctx)`) and imports the nested class.
        direct = bool(re.fullmatch(r"(?:[\w.]+\.)?CustomPayloadEvent\.Context", pt)) or (
            pt == "Context" and re.search(r"(?m)^import\s+[\w.]*\.(?:CustomPayloadEvent|NetworkEvent)\.Context\s*;", text) is not None) \
            or pt == "NetworkEvent.Context"     # a handler the X104 unwrapping exposed: the context comes in directly
        # Vineflower imports the nested class (`import ...NetworkEvent.Context;`) and writes `Supplier<Context>`
        nested = re.search(r"(?m)^import\s+[\w.]*\.NetworkEvent\.Context\s*;", text) is not None
        if not direct and not re.fullmatch(r"Supplier<\s*NetworkEvent\.Context\s*>", pt) \
                and not (nested and re.fullmatch(r"Supplier<\s*Context\s*>", pt)):
            continue
        cs = fs.pname(mt.params[-1])
        body = text[mt.body_open:mt.body_close + 1]
        alias = re.search(r"\n[ \t]*(?:NetworkEvent\.)?Context\s+(\w+)\s*=\s*%s\.get\(\)\s*;[ \t]*" % re.escape(cs), body)
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


def _line_start(t, i):
    return t.rfind("\n", 0, i) + 1


def _cut_method(t, mt):
    """Remove method `mt` together with the annotation lines directly above it."""
    start = _line_start(t, mt.start)
    while True:
        prev = _line_start(t, start - 1) if start > 0 else 0
        if start == 0 or not re.match(r"[ \t]*@", t[prev:start]):
            break
        start = prev
    end = mt.body_close + 1
    end = end + 1 if t[end:end + 1] == "\n" else end
    return t[:start] + t[end:]


def place_listener(new, ref):
    """Add `<bus>.addListener(<ref>);` at the end of the @Mod class constructor that has the mod bus. -> file or None."""
    for f, t in new.items():
        if not re.search(r"(?m)^@Mod\(", t):
            continue
        for cm in re.finditer(r"(?m)^[ \t]*(?:(?:public|protected|private)\s+)?%s\s*\(" % re.escape(f.stem), t):
            p1 = match(t, cm.end() - 1)
            b = re.match(r"\s*\{", t[p1 + 1:]) if p1 > 0 else None
            if not b:
                continue
            b1 = match(t, p1 + b.end())
            bus = buses_in_scope(t, b1 - 1) if b1 > 0 else []
            if bus:
                ind = re.match(r"[ \t]*", t[t.rfind("\n", 0, b1) + 1:]).group(0)
                new[f] = t[:b1] + f"   {bus[-1]}.addListener({ref});\n{ind}" + t[b1:]
                return f
    return None


def normalise_late_channel(new):
    """`private static SimpleChannel INSTANCE;` assigned once inside a method (`INSTANCE = ChannelBuilder...;`):
    move the initialiser onto the field, so the converter sees the usual `SimpleChannel X = ...` shape (X102)."""
    for f in list(new):
        t = new[f]
        for dm in re.finditer(r"(?m)^([ \t]*(?:(?:public|private|protected|static)\s+)*)SimpleChannel\s+(\w+)\s*;", t):
            chan = dm.group(2)
            am = list(re.finditer(r"(?m)^[ \t]*(?:this\.|\w+\.)?%s\s*=\s*(?=NetworkRegistry\.newSimpleChannel\(|ChannelBuilder\b|NetworkRegistry\.ChannelBuilder\b)" % re.escape(chan), t))
            if len(am) != 1:
                continue
            a = am[0]
            e = a.end()
            while e < len(t) and t[e] != ";":
                k = fs._skip(t, e)
                if k != e:
                    e = k; continue
                e = match(t, e) + 1 if t[e] in "({[" else e + 1
            expr = t[a.end():e]
            ls = t.rfind("\n", 0, a.start()) + 1
            t = t[:ls] + t[e + 1:].removeprefix("\n")
            t = t[:dm.start()] + f"{dm.group(1)}SimpleChannel {chan} = {expr};" + t[dm.end():]
            new[f] = t
            break


def normalise_mcreator(new, src, flags, report):
    """MCreator's channel (X97): a `<T> addNetworkMessage(Class<T>, encoder, decoder, consumer)` helper that calls
    CHAN.registerMessage(messageID, ...) with VARIABLES, and one `Owner.addNetworkMessage(P.class, ...)` per message,
    each in that message's own @SubscribeEvent FMLCommonSetupEvent method. Rewrite it into the classic shape the
    converter handles: the helper becomes `registerMessages()` holding one `CHAN.registerMessage(n, P.class, ...)` per
    message, the per-message subscribers go (and an @EventBusSubscriber left with no handler loses its annotation,
    catalogue R1), and `Owner.registerMessages();` is placed where the mod bus is in scope. An unused helper (MCreator
    generates it in every mod) is deleted with its counter and channel."""
    for f in list(new):
        t = new[f]
        hm = next((mt for mt in methods(t) if mt.name == "addNetworkMessage"
                   and re.search(r"\bClass<\s*\w+\s*>", t[mt.params_open:mt.params_close])), None)
        if hm is None:
            continue
        cm = re.search(r"(\w+)\s*\.\s*registerMessage\(", t[hm.body_open:hm.body_close])
        if not cm:
            continue
        chan, owner, rel = cm.group(1), f.stem, f.relative_to(src).as_posix()
        regs = []
        for g in list(new):
            u = new[g]
            for m in reversed(list(re.finditer(r"(?<![\w$])(?:(?:[\w.]+\.)?%s\.)?addNetworkMessage\(" % re.escape(owner), u))):
                if g == f and hm.body_open <= m.start() <= hm.body_close:
                    continue
                if g == f and m.start() >= hm.start and m.start() < hm.body_open:
                    continue
                p0 = m.end() - 1; p1 = match(u, p0)
                args = [a.strip() for a in split_args(u[p0 + 1:p1])]
                semi = u.find(";", p1)
                if len(args) != 4 or not args[0].endswith(".class") or semi < 0 or u[p1 + 1:semi].strip():
                    flags.append(f"{g.relative_to(src).as_posix()}:{fs.line_of(u, m.start())} addNetworkMessage(...) not a plain registration")
                    continue
                regs.append(args)
                ls = _line_start(u, m.start())
                le = semi + 1 + (1 if u[semi + 1:semi + 2] == "\n" else 0)
                u = u[:ls] + u[le:] if not u[ls:m.start()].strip() else u[:m.start()] + u[semi + 1:]
            if u is not new[g] and u != new[g]:
                # a setup subscriber the registration was all of: remove it; an @EventBusSubscriber left without any
                # @SubscribeEvent method would crash mod construction (R1), so its annotation goes too
                for mt in sorted(methods(u), key=lambda x: -x.start):
                    if (not u[mt.body_open + 1:mt.body_close].strip() and re.search(r"\bFMLCommonSetupEvent\b",
                            u[mt.params_open:mt.params_close])):
                        u = _cut_method(u, mt)
                if not re.search(r"(?m)^[ \t]*@SubscribeEvent\b", u):
                    u = re.sub(r"(?m)^[ \t]*@(?:Mod\.)?EventBusSubscriber\b(?:\([^)]*\))?[ \t]*\n", "", u)
                new[g] = u
        t = new[f]
        hm = next(mt for mt in methods(t) if mt.name == "addNetworkMessage")
        if not regs:
            # generated and never used: drop the helper, its counter and the channel
            t = _cut_method(t, hm)
            t = re.sub(r"(?m)^[ \t]*(?:(?:private|public|protected|static)\s+)+int\s+messageID\s*=\s*0\s*;[ \t]*\n", "", t)
            dm = re.search(r"(?m)^[ \t]*(?:(?:public|private|protected|static|final)\s+)*SimpleChannel\s+%s\s*=" % re.escape(chan), t)
            if dm and not re.search(r"(?<![\w$])%s\b" % re.escape(chan), t[:dm.start()] + t[t.find(";", dm.end()) + 1:]) \
                    and not any(re.search(r"\b%s\b" % re.escape(chan), u) for g, u in new.items() if g != f):
                e = dm.end()
                while t[e] != ";":
                    e = match(t, e) + 1 if t[e] in "({[" else e + 1
                t = t[:dm.start()] + t[e + 1:].removeprefix("\n")
                report["unused mcreator channels"] += 1
            else:
                flags.append(f"{rel}: {chan} declared with no registrations but still used")
            new[f] = t
            continue
        ind = re.match(r"[ \t]*", t[_line_start(t, hm.start):]).group(0)
        body = "".join(f"\n{ind}    {chan}.registerMessage({i}, {', '.join(a)});" for i, a in enumerate(regs))

        t = t[:_line_start(t, hm.start)] + f"{ind}public static void registerMessages() {{{body}\n{ind}}}\n" + t[hm.body_close + 2:]
        # the registrations were written in each message's own file: import the classes they name (X103) -- AFTER the
        # splice above, which uses offsets taken before any import is added
        own_pkg = (re.search(r"(?m)^package\s+([\w.]+)\s*;", t) or [None, None])[1]
        for a in regs:
            first = re.match(r"([A-Z]\w*)", a[0])
            g = next((x for x in new if first and x.stem == first.group(1)), None)
            pm = re.search(r"(?m)^package\s+([\w.]+)\s*;", new[g]) if g else None
            if pm and pm.group(1) != own_pkg:
                t = add_import(t, f"{pm.group(1)}.{first.group(1)}")
        t = re.sub(r"(?m)^[ \t]*(?:(?:private|public|protected|static)\s+)+int\s+messageID\s*=\s*0\s*;[ \t]*\n", "", t)
        # the call that step 5 turns into bus.addListener(Owner::registerMessages): in the @Mod constructor, after its bus
        close = None          # methods() skips constructors: find `Owner(...) {` with the mod bus in scope at its end
        for cm2 in re.finditer(r"(?m)^[ \t]*(?:(?:public|protected|private)\s+)?%s\s*\(" % re.escape(owner), t):
            p1 = match(t, cm2.end() - 1)
            b = re.match(r"\s*\{", t[p1 + 1:])
            if p1 > 0 and b:
                b0 = p1 + b.end(); b1 = match(t, b0)
                if b1 > 0 and buses_in_scope(t, b1 - 1):
                    close = b1; break
        if close is None:
            flags.append(f"{rel}: no constructor with the mod bus in scope; add `modBus.addListener({owner}::registerMessages)`")
        else:
            t = t[:close] + f"    {owner}.registerMessages();\n{ind}" + t[close:]
        new[f] = t
        report["mcreator registrations"] += len(regs)


def normalise_helper_channel(new, flags, report):
    """X154: the channel registered through a BASE class's helpers (a Mekanism-lineage shape, Blood Magic's):
        abstract class Base { protected abstract SimpleChannel getChannel(); public abstract void initialize();
            protected <MSG> void registerServerToClient(Class<MSG> type, enc, dec, consumer) {
                this.getChannel().registerMessage(this.index++, type, enc, dec, consumer, Optional.of(NetworkDirection.PLAY_TO_CLIENT)); }
            public <MSG> void sendTo(MSG m, ServerPlayer p) { this.getChannel().sendTo(m, p.connection.connection, ...); } }
        class Handler extends Base { public static final SimpleChannel INSTANCE = createChannel(rl);
            @Override public void initialize() { this.registerServerToClient(P.class, P::encode, P::decode, P::handle); ... }
            @Override protected SimpleChannel getChannel() { return INSTANCE; } }
        Mod { static final Handler packetHandler = new Handler(); ... packetHandler.initialize(); }
    is rewritten into the shape the converter knows -- the channel field built by ChannelBuilder, one
    `INSTANCE.registerMessage(0, P.class, enc, dec, h, Optional.of(dir))` per helper call, in a STATIC initialize()
    called as `Handler.initialize();` -- and the base loses what only served the channel (the helpers, getChannel,
    createChannel, the abstract initialize). Its sends (`this.getChannel().send(..)`) are rewritten with the rest."""
    for base_f in list(new):
        bt = new[base_f]
        if not re.search(r"abstract\s+SimpleChannel\s+getChannel\s*\(\s*\)\s*;", bt):
            continue
        base = base_f.stem
        helpers = {}
        for mt in methods(bt):
            body = bt[mt.body_open:mt.body_close]
            rm = re.search(r"getChannel\(\)\s*\.\s*registerMessage\(", body)
            if not rm or len(mt.params) != 4:
                continue
            dm = re.search(r"NetworkDirection\.(PLAY_TO_CLIENT|PLAY_TO_SERVER)", body)
            helpers[mt.name] = dm.group(1) if dm else None
        if not helpers:
            continue
        subs = [(f, t) for f, t in new.items() if re.search(r"\bextends\s+%s\b" % re.escape(base), t)]
        if len(subs) != 1:
            flags.append(f"{base_f.name}: {len(subs)} subclasses of {base}; helper-registered channel not converted")
            continue
        sf, st = subs[0]
        sub = sf.stem
        fm = re.search(r"(SimpleChannel\s+(\w+)\s*=\s*)createChannel\(", st)
        gm = re.search(r"SimpleChannel\s+getChannel\s*\(\s*\)\s*\{\s*return\s+(\w+)\s*;\s*\}", st)
        if not fm or not gm or gm.group(1) != fm.group(2):
            flags.append(f"{sf.name}: channel field / getChannel() not in the expected shape; helper-registered channel not converted")
            continue
        chan = fm.group(2)
        p0 = fm.end() - 1; p1 = match(st, p0)
        st = st[:fm.start()] + f"{fm.group(1)}ChannelBuilder.named({st[p0 + 1:p1].strip()}).simpleChannel()" + st[p1 + 1:]
        # helper calls -> registerMessage on the channel
        for name, d in helpers.items():
            def call(m, d=d):
                q0 = m.end() - 1; q1 = match(m.string, q0)
                return None if q1 < 0 else (m.start(), q1 + 1, m.string[q0 + 1:q1])
            for m in reversed(list(re.finditer(r"(?<![\w$])(?:this\.)?%s\s*\(" % re.escape(name), st))):
                c = call(m)
                if not c:
                    continue
                dpart = f", Optional.of(NetworkDirection.{d})" if d else ""
                st = st[:c[0]] + f"{chan}.registerMessage(0, {c[2].strip()}{dpart})" + st[c[1]:]
                report["helper registrations"] += 1
        # initialize(): static, no @Override; getChannel() override gone
        st = re.sub(r"(?m)^([ \t]*)@Override\s*\n([ \t]*)public\s+void\s+initialize\s*\(", r"\2public static void initialize(", st)
        st = re.sub(r"(?m)^([ \t]*)public\s+void\s+initialize\s*\(", r"\1public static void initialize(", st)
        st = re.sub(r"\n[ \t]*(?:@Override\s*\n[ \t]*)?(?:public|protected)\s+SimpleChannel\s+getChannel\s*\(\s*\)\s*\{\s*return\s+\w+\s*;\s*\}[ \t]*", "", st)
        if "Optional.of(" in st and not re.search(r"(?m)^import\s+java\.util\.Optional\s*;", st):
            st = add_import(st, "java.util.Optional")
        if "NetworkDirection." in st and "NetworkDirection;" not in st:
            st = add_import(st, "net.minecraftforge.network.NetworkDirection")
        new[sf] = st
        # the base: drop the helpers, createChannel, getChannel, the abstract initialize
        for mt in sorted(methods(bt), key=lambda x: -x.start):
            if mt.name in helpers or mt.name == "createChannel":
                s = fs.decl_span_start(bt, mt.start)
                bt = bt[:s] + bt[mt.body_close + 1:]
        bt = re.sub(r"\n[ \t]*(?:protected|public)\s+abstract\s+(?:SimpleChannel\s+getChannel|void\s+initialize)\s*\(\s*\)\s*;[ \t]*", "", bt)
        bt = re.sub(r"\n[ \t]*private\s+int\s+index\s*=\s*0\s*;[ \t]*", "", bt)
        for mt in sorted(methods(bt), key=lambda x: -x.start):       # context helpers (`getPlayer(Supplier<Context> c)`)
            if mt.params and "Context" in mt.params[-1]:
                bt, _k = convert_handler(bt, mt.name, flags, base_f.name)
        new[base_f] = bt
        # call sites: `<field of type Sub>.initialize();` -> `Sub.initialize();`
        fields = set()
        for t2 in new.values():
            fields |= set(re.findall(r"\b%s\s+(\w+)\s*=\s*new\s+%s\s*\(" % (re.escape(sub), re.escape(sub)), t2))
        for g in list(new):
            t2 = new[g]
            for fld in fields:
                t2 = re.sub(r"(?<![\w$])(?:[\w.]+\.)?%s\s*\.\s*initialize\s*\(\s*\)\s*;" % re.escape(fld), f"{sub}.initialize();", t2)
            t2 = re.sub(r"(?<![\w$])new\s+%s\s*\(\s*\)\s*\.\s*initialize\s*\(\s*\)\s*;" % re.escape(sub), f"{sub}.initialize();", t2)
            new[g] = t2
        report["helper channels"] += 1
        return "getChannel()"
    return None


def run(src, modid, dry=False):
    src = pathlib.Path(src).resolve()
    files = {f: f.read_text(encoding="utf-8") for f in src.rglob("*.java")}
    new = dict(files)
    flags, report = [], collections.Counter()
    used_ids = set()
    normalise_late_channel(new)
    normalise_mcreator(new, src, flags, report)
    helper_alias = normalise_helper_channel(new, flags, report)
    for f in files:
        # the CURRENT text: an earlier channel's send rewrite (step 4) may already have edited this file, and every
        # offset below is taken from what is spliced -- offsets from the original text cut one mod's registration
        # in half (X81)
        t = new[f]
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
                outer = (hm.group(1) or "").rsplit(".", 1)[-1]
                simple_cls = c.cls.rsplit(".", 1)[-1]
                # `P.Handler::handle` -- a handler class nested in the packet (or in another named class)
                hf = pf if simple_cls in (hm.group(2), outer) else \
                    (class_file(outer, files, f) if outer else None) or class_file(hm.group(2), files, f)
                if hf == pf:
                    txt, k = convert_handler(txt, hm.group(3), flags, pf.relative_to(src).as_posix())
                else:
                    new[pf] = txt
                    t2, k = convert_handler(new[hf], hm.group(3), flags, hf.relative_to(src).as_posix())
                    new[hf] = t2; txt = new[pf]
                if not k and not re.search(r"\b%s\s*\([^)]*\bIPayloadContext\b" % re.escape(hm.group(3)),
                                           txt if hf == pf else new[hf]):
                    # say so: a handler whose context shape is not recognised is otherwise left untouched without a word
                    flags.append(f"{rel}: handler {hm.group(2)}::{hm.group(3)} has no recognised context parameter; not converted")
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
            if "->" in c.handler:
                flags.append(f"{rel}: {c.cls.rsplit('.', 1)[-1]}'s handler is a lambda kept as written; its context use needs porting")
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
        # a LOCAL channel stored into a field (`SimpleChannel net = ...; INSTANCE = net;`): the alias and its field go too
        aliases = re.findall(r"(?m)^[ \t]*(\w+)\s*=\s*%s\s*;[ \t]*$" % re.escape(chan), t)
        for alias in aliases:
            t = re.sub(r"(?m)^[ \t]*%s\s*=\s*%s\s*;[ \t]*\n" % (re.escape(alias), re.escape(chan)), "", t)
            t = re.sub(r"(?m)^[ \t]*(?:(?:public|private|protected|static|final)\s+)*SimpleChannel\s+%s\s*;[ \t]*\n"
                       % re.escape(alias), "", t)
        for imp in ("net.neoforged.neoforge.network.event.RegisterPayloadHandlersEvent",
                    "net.neoforged.neoforge.network.registration.PayloadRegistrar"):
            t = add_import(t, imp)
        if "HandlerThread." in t:
            t = add_import(t, "net.neoforged.neoforge.network.registration.HandlerThread")
        new[f] = t
        # 4. sends, everywhere
        for g in list(new):
            t2, k = new[g], 0
            for name in [chan] + aliases + ([helper_alias] if helper_alias else []):   # X137 field alias, X154 base getChannel()
                t2, k1 = rewrite_sends(t2, name, flags, g.relative_to(src).as_posix())
                k += k1
            if k:
                # X125: the sends just written are NeoForge's; a Forge PacketDistributor import (kept because the old
                # calls used it) would shadow the new one, and add_import refuses a second class of that name
                t2 = re.sub(r"(?m)^import\s+net\.minecraftforge\.network\.PacketDistributor\s*;[ \t]*\n", "", t2)
                t2 = add_import(t2, "net.neoforged.neoforge.network.PacketDistributor")
                t2 = re.sub(r"(public\s+(?:static\s+)?)<(\w+)>(\s+void\s+\w+\(\s*\2\s+\w+)", r"\1<\2 extends CustomPacketPayload>\3", t2)
                # a helper taking the message as Object (`sendToPlayer(Object packet, ServerPlayer p)`) must take a payload now:
                # retype the parameter a PacketDistributor call sends, when exactly one Object parameter has that name
                for nm in set(re.findall(r"PacketDistributor\.sendTo\w*\((?:[^;]*?,\s*)?(\w+)\)\s*;", t2)):
                    hit = list(re.finditer(r"(?<=[(,])(\s*)Object(\s+%s\s*[,)])" % re.escape(nm), t2))
                    if len(hit) == 1:
                        t2 = t2[:hit[0].start()] + hit[0].group(1) + "CustomPacketPayload" + hit[0].group(2) + t2[hit[0].end():]
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
                    # called from a setup handler with no mod bus in scope: register from the @Mod constructor instead
                    # (the mod bus is there), and drop the old call -- RegisterPayloadHandlersEvent fires on its own (X102)
                    before = new[g]       # cut the old call FIRST: placing the listener may shift offsets in this file
                    ls = before.rfind("\n", 0, m.start()) + 1
                    line_only = not before[ls:m.start()].strip()
                    new[g] = (before[:ls] + before[m.end():].lstrip(" \t").removeprefix("\n") if line_only
                              else before[:m.start()] + before[m.end():])
                    if place_listener(new, f"{cname}::{owner.name}"):
                        report["call sites"] += 1
                    else:
                        new[g] = before
                        flags.append(f"{g.relative_to(src).as_posix()}:{fs.line_of(new[g], m.start())} {cname}.{owner.name}() "
                                     "has no IEventBus in scope: add `modBus.addListener(" + cname + "::" + owner.name + ")`")
                break
    # a channel shape none of the above recognised must not pass silently (X102): name every file still holding one
    for f, t in new.items():
        if re.search(r"\bSimpleChannel\s+\w+\s*[=;]", t):
            flags.append(f"{f.relative_to(src).as_posix()}: a SimpleChannel this converter did not recognise is still declared")
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
import net.minecraftforge.network.PacketDistributor;
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
        net = (r / "Net.java").read_text(encoding="utf-8"); ping = (r / "PingPacket.java").read_text(encoding="utf-8"); pong = (r / "PongPacket.java").read_text(encoding="utf-8")
        mod = (r / "Mod.java").read_text(encoding="utf-8")
        want = [(net, "public static void register(RegisterPayloadHandlersEvent event) {"),
                (net, "import net.neoforged.neoforge.network.PacketDistributor;"),   # X125: replaces Forge's
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
        if "import net.minecraftforge.network.PacketDistributor;" in net:
            miss.append("Forge PacketDistributor import left beside the NeoForge sends (X125)")
        for bad, t in (("SimpleChannel", net), ("NetworkEvent", ping), ("setPacketHandled", pong), ("Supplier", pong),
                       ("CHANNEL", net)):
            if bad in t:
                miss.append(f"still has {bad}")
        before = {p: p.read_text(encoding="utf-8") for p in r.iterdir()}
        run(d, "mymod")
        if any(p.read_text(encoding="utf-8") != before[p] for p in r.iterdir()):
            miss.append("not idempotent")
        ok = not miss and not flags
    with tempfile.TemporaryDirectory() as d:       # two channel files; one keeps its channel in a local (X81)
        root = pathlib.Path(d) / "m/net"; root.mkdir(parents=True)
        def pk(n):
            return (f"package m.net;\nimport net.minecraft.network.FriendlyByteBuf;\nimport java.util.function.Supplier;\n"
                    f"public class {n} {{\n   public {n}(FriendlyByteBuf b) {{}}\n   public void toBytes(FriendlyByteBuf b) {{}}\n"
                    f"   public boolean handle(Supplier<NetworkEvent.Context> s) {{ return true; }}\n}}\n")
        (root / "A.java").write_text(pk("A"), encoding="utf-8"); (root / "B.java").write_text(pk("B"), encoding="utf-8")
        (root / "Net.java").write_text(
            "package m.net;\npublic class Net {\n   public static final SimpleChannel CH = NetworkRegistry.newSimpleChannel(x, () -> \"1\", s -> true, s -> true);\n"
            "   public static void register() {\n      CH.messageBuilder(A.class, 0, NetworkDirection.PLAY_TO_CLIENT).decoder(A::new).encoder(A::toBytes).consumerMainThread(A::handle).add();\n   }\n"
            "   public static void send(Object o) { Msgs.INSTANCE.send(PacketDistributor.ALL.noArg(), o); }\n}\n", encoding="utf-8")
        (root / "Msgs.java").write_text(
            "package m.net;\npublic class Msgs {\n   public static SimpleChannel INSTANCE;\n   public static void register() {\n"
            "      SimpleChannel net = ChannelBuilder.named(x)\n         .simpleChannel();\n      INSTANCE = net;\n"
            "      net.messageBuilder(B.class, 1, NetworkDirection.PLAY_TO_CLIENT)\n         .decoder(B::new)\n         .encoder(B::toBytes)\n"
            "         .consumerMainThread(B::handle)\n         .add();\n   }\n"
            "   public static <MSG> void to(MSG m, ServerPlayer p) {\n      INSTANCE.sendTo(m, p.connection.connection, NetworkDirection.PLAY_TO_CLIENT);\n   }\n}\n",
            encoding="utf-8")
        run(d, "m")
        m2 = (root / "Msgs.java").read_text(encoding="utf-8")
        ok = ok and "registrar.playToClient(B.TYPE, B.STREAM_CODEC, B::handle);" in m2 and ".consumerMainThread" not in m2 \
            and "INSTANCE" not in m2 and "SimpleChannel" not in m2
        # X137: the sends go through the field the local was stored in, here and in another class
        ok = ok and "PacketDistributor.sendToPlayer(p, m);" in m2 and "<MSG extends CustomPacketPayload>" in m2 \
            and "PacketDistributor.sendToAllPlayers(o)" in (root / "Net.java").read_text(encoding="utf-8")
    import tempfile
    with tempfile.TemporaryDirectory() as d:     # Forge 1.20.2+: ChannelBuilder, instance handle(CustomPayloadEvent.Context),
        p = pathlib.Path(d) / "m/net"           # send(message, target), and a helper taking the message as Object
        p.mkdir(parents=True)
        (p / "Net.java").write_text(
            "package m.net;\nimport net.minecraft.server.level.ServerPlayer;\nimport net.neoforged.neoforge.network.ChannelBuilder;\n"
            "import net.neoforged.neoforge.network.NetworkDirection;\nimport net.neoforged.neoforge.network.SimpleChannel;\n"
            "public class Net {\n    public static final SimpleChannel network = ChannelBuilder.named(new ResourceLocation(\"m\", \"main\"))"
            ".networkProtocolVersion(1).simpleChannel();\n    private static int id = 0;\n    public static void setup() {\n"
            "        network.messageBuilder(Skip.class, id++, NetworkDirection.PLAY_TO_CLIENT).encoder(Skip::encode).decoder(Skip::decode)"
            ".consumerMainThread(Skip::handle).add();\n    }\n"
            "    public static void sendToPlayer(Object packet, ServerPlayer player) {\n"
            "        network.send(packet, player.connection.getConnection());\n    }\n}\n", encoding="utf-8")
        (p / "Skip.java").write_text(
            "package m.net;\nimport net.minecraft.network.FriendlyByteBuf;\nimport net.neoforged.neoforge.event.network.CustomPayloadEvent;\n"
            "public class Skip {\n    public static Skip decode(FriendlyByteBuf buf) { return new Skip(); }\n"
            "    public void encode(FriendlyByteBuf buf) { }\n"
            "    public void handle(CustomPayloadEvent.Context context) { context.enqueueWork(() -> {}); }\n}\n", encoding="utf-8")
        (p / "Skip2.java").write_text(
            "package m;\nimport net.minecraft.network.FriendlyByteBuf;\nimport net.neoforged.neoforge.event.network.CustomPayloadEvent.Context;\n"
            "public class Skip2 {\n    public static Skip2 decode(FriendlyByteBuf buf) { return new Skip2(); }\n"
            "    public void encode(FriendlyByteBuf buf) { }\n    public void handle(Context context) { }\n}\n", encoding="utf-8")
        nt = (p / "Net.java").read_text(encoding="utf-8").replace(".add();\n    }", ".add();\n        network.messageBuilder("
              "Skip2.class, id++, NetworkDirection.PLAY_TO_CLIENT).encoder(Skip2::encode).decoder(Skip2::decode)"
              ".consumerMainThread(Skip2::handle).add();\n    }", 1)
        (p / "Net.java").write_text(nt, encoding="utf-8")
        r2, f2, _c = run(d, "m")
        k2b = (p / "Skip2.java").read_text(encoding="utf-8")
        n2, k2 = (p / "Net.java").read_text(encoding="utf-8"), (p / "Skip.java").read_text(encoding="utf-8")
        ok2 = ("registrar.playToClient(Skip.TYPE, Skip.STREAM_CODEC, Skip::handle);" in n2
               and "PacketDistributor.sendToPlayer(player, packet);" in n2 and "CustomPacketPayload packet" in n2
               and "SimpleChannel" not in n2 and "public void handle(IPayloadContext context)" in k2
               and "CustomPayloadEvent" not in k2 and not f2
               and "public void handle(IPayloadContext context)" in k2b and "CustomPayloadEvent" not in k2b)
        if not ok2:
            print("self-check (1.20.2 shape):", f2, "\n" + n2 + "\n" + k2)
        ok = ok and ok2
    with tempfile.TemporaryDirectory() as d:     # X97: MCreator's addNetworkMessage helper, used and unused
        base = pathlib.Path(d)
        for sub, used in (("a", True), ("b", False)):
            p = base / sub / "m"; p.mkdir(parents=True)
            (p / "MMod.java").write_text(
                "package m;\nimport net.neoforged.bus.api.IEventBus;\nimport net.neoforged.neoforge.network.NetworkRegistry;\n"
                "import net.neoforged.neoforge.network.simple.SimpleChannel;\n@Mod(\"m\")\npublic class MMod {\n"
                "    public static final SimpleChannel PACKET_HANDLER = NetworkRegistry.newSimpleChannel(new ResourceLocation(\"m\", \"m\"), () -> \"1\", \"1\"::equals, \"1\"::equals);\n"
                "    private static int messageID = 0;\n    public MMod() {\n        IEventBus bus = FMLJavaModLoadingContext.get().getModEventBus();\n"
                "        Things.REGISTRY.register(bus);\n    }\n"
                "    public static <T> void addNetworkMessage(Class<T> messageType, BiConsumer<T, FriendlyByteBuf> encoder, "
                "Function<FriendlyByteBuf, T> decoder, BiConsumer<T, Supplier<NetworkEvent.Context>> messageConsumer) {\n"
                "        PACKET_HANDLER.registerMessage(messageID, messageType, encoder, decoder, messageConsumer);\n        ++messageID;\n    }\n}\n",
                encoding="utf-8")
            if used:
                (p / "KeyMessage.java").write_text(
                    "package m;\nimport java.util.function.Supplier;\nimport net.minecraft.network.FriendlyByteBuf;\n"
                    "import net.neoforged.neoforge.network.NetworkEvent.Context;\n"
                    "@Mod.EventBusSubscriber(bus=Mod.EventBusSubscriber.Bus.MOD)\npublic class KeyMessage {\n    int type;\n"
                    "    public KeyMessage(int type) { this.type = type; }\n    public KeyMessage(FriendlyByteBuf buffer) { this.type = buffer.readInt(); }\n"
                    "    public static void buffer(KeyMessage message, FriendlyByteBuf buffer) { buffer.writeInt(message.type); }\n"
                    "    public static void handler(KeyMessage message, Supplier<Context> contextSupplier) {\n"
                    "        Context context = contextSupplier.get();\n        context.enqueueWork(() -> {});\n"
                    "        context.setPacketHandled(true);\n    }\n    @SubscribeEvent\n    public static void registerMessage(FMLCommonSetupEvent event) {\n"
                    "        MMod.addNetworkMessage(KeyMessage.class, KeyMessage::buffer, KeyMessage::new, KeyMessage::handler);\n    }\n}\n",
                    encoding="utf-8")
                (p / "Keys.java").write_text("package m;\nclass Keys { void k() { MMod.PACKET_HANDLER.sendToServer(new KeyMessage(0)); } }\n",
                                             encoding="utf-8")
            r3, f3, _c = run(base / sub, "m")
            mm = (p / "MMod.java").read_text(encoding="utf-8")
            if used:
                km = (p / "KeyMessage.java").read_text(encoding="utf-8")
                ok3 = ("registrar.playToServer(KeyMessage.TYPE, KeyMessage.STREAM_CODEC, KeyMessage::handler);" in mm
                       and "bus.addListener(MMod::registerMessages);" in mm and "addNetworkMessage" not in mm + km
                       and "messageID" not in mm and "EventBusSubscriber" not in km and "FMLCommonSetupEvent event" not in km
                       and "handler(KeyMessage message, IPayloadContext context)" in km and "NetworkEvent" not in km
                       and "PacketDistributor.sendToServer(new KeyMessage(0))" in (p / "Keys.java").read_text(encoding="utf-8")
                       and not f3)
            else:
                ok3 = "PACKET_HANDLER" not in mm and "addNetworkMessage" not in mm and "messageID" not in mm and not f3
            if not ok3:
                print(f"self-check X97 ({'used' if used else 'unused'}):", f3, "\n" + mm)
            ok = ok and ok3
    with tempfile.TemporaryDirectory() as d:     # X102: late-assigned channel, nested Handler, register() from setup
        p = pathlib.Path(d) / "c"; p.mkdir()
        (p / "CMod.java").write_text(
            "package c;\n@Mod(\"c\")\npublic class CMod {\n   public CMod(IEventBus modEventBus, ModContainer c) {\n"
            "      modEventBus.addListener(this::commonSetup);\n   }\n\n   private void commonSetup(FMLCommonSetupEvent event) {\n"
            "      Msgs.register();\n   }\n}\n", encoding="utf-8")
        (p / "Msgs.java").write_text(
            "package c;\npublic final class Msgs {\n   private static SimpleChannel INSTANCE;\n   public static void register() {\n"
            "      INSTANCE = ChannelBuilder.named(new ResourceLocation(\"c\", \"main\")).networkProtocolVersion(1).simpleChannel();\n"
            "      INSTANCE.messageBuilder(Undo.class, 0, NetworkDirection.PLAY_TO_CLIENT)\n         .encoder(Undo::encode)\n"
            "         .decoder(Undo::decode)\n         .consumerMainThread(Undo.Handler::handle)\n         .add();\n   }\n}\n",
            encoding="utf-8")
        (p / "Undo.java").write_text(
            "package c;\nimport net.neoforged.neoforge.event.network.CustomPayloadEvent.Context;\npublic class Undo {\n"
            "   public static Undo decode(FriendlyByteBuf buf) { return new Undo(); }\n   public void encode(FriendlyByteBuf buf) { }\n"
            "   public static class Handler {\n      public static void handle(Undo msg, Context ctx) { }\n   }\n}\n", encoding="utf-8")
        r4, f4, _c = run(d, "c")
        cm_, ms_, un_ = ((p / n).read_text(encoding="utf-8") for n in ("CMod.java", "Msgs.java", "Undo.java"))
        ok4 = ("modEventBus.addListener(Msgs::register);" in cm_ and "Msgs.register();" not in cm_
               and "registrar.playToClient(Undo.TYPE, Undo.STREAM_CODEC, Undo.Handler::handle);" in ms_
               and "SimpleChannel" not in ms_ and "INSTANCE" not in ms_
               and "public static void handle(Undo msg, IPayloadContext ctx)" in un_ and not f4)
        if not ok4:
            print("self-check X102:", f4, "\n" + cm_ + ms_ + un_)
        ok = ok and ok4
    ok5 = (unwrap_handler("(pkt, sup) -> {\n   Context ctx = (Context)sup.get();\n   ctx.enqueueWork(() -> P.handle(pkt, ctx));\n"
                          "   ctx.setPacketHandled(true);\n}") == "P::handle"
           and unwrap_handler("(m, s) -> { s.get().enqueueWork(() -> go()); }").startswith("(m, s)"))
    if not ok5:
        print("self-check X104: unwrap_handler")
    ok = ok and ok5
    with tempfile.TemporaryDirectory() as d:     # X154: registration through a base class's helpers
        q = pathlib.Path(d) / "m/net"; q.mkdir(parents=True)
        (q / "Base.java").write_text(
            "package m.net;\nimport java.util.Optional;\nimport java.util.function.Supplier;\nimport net.minecraftforge.network.NetworkDirection;\n"
            "import net.minecraftforge.network.PacketDistributor;\nimport net.minecraftforge.network.NetworkEvent.Context;\n"
            "import net.minecraftforge.network.NetworkRegistry.ChannelBuilder;\nimport net.minecraftforge.network.simple.SimpleChannel;\n"
            "public abstract class Base {\n   private int index = 0;\n"
            "   protected static SimpleChannel createChannel(ResourceLocation name) {\n      return ChannelBuilder.named(name).simpleChannel();\n   }\n"
            "   public static Player getPlayer(Supplier<Context> context) {\n      return context.get().getSender();\n   }\n"
            "   protected abstract SimpleChannel getChannel();\n   public abstract void initialize();\n"
            "   protected <MSG> void registerServerToClient(Class<MSG> type, BiConsumer<MSG, FriendlyByteBuf> encoder, "
            "Function<FriendlyByteBuf, MSG> decoder, BiConsumer<MSG, Supplier<Context>> consumer) {\n"
            "      this.getChannel().registerMessage(this.index++, type, encoder, decoder, consumer, Optional.of(NetworkDirection.PLAY_TO_CLIENT));\n   }\n"
            "   public <MSG> void sendToAll(MSG message) {\n      this.getChannel().send(PacketDistributor.ALL.noArg(), message);\n   }\n}\n",
            encoding="utf-8")
        (q / "Handler.java").write_text(
            "package m.net;\nimport net.minecraftforge.network.simple.SimpleChannel;\npublic class Handler extends Base {\n"
            "   public static final SimpleChannel INSTANCE = createChannel(new ResourceLocation(\"m\", \"main\"));\n"
            "   @Override\n   public void initialize() {\n      this.registerServerToClient(P.class, P::encode, P::decode, P::handle);\n   }\n"
            "   @Override\n   protected SimpleChannel getChannel() {\n      return INSTANCE;\n   }\n}\n", encoding="utf-8")
        (q / "P.java").write_text(
            "package m.net;\nimport java.util.function.Supplier;\nimport net.minecraft.network.FriendlyByteBuf;\n"
            "import net.minecraftforge.network.NetworkEvent.Context;\npublic class P {\n"
            "   public static void encode(P p, FriendlyByteBuf b) {}\n   public static P decode(FriendlyByteBuf b) { return new P(); }\n"
            "   public static void handle(P p, Supplier<Context> c) { c.get().setPacketHandled(true); }\n}\n", encoding="utf-8")
        (pathlib.Path(d) / "m/Mod.java").write_text(
            "package m;\n@Mod(\"m\")\npublic class Mod {\n   public static final Handler packetHandler = new Handler();\n"
            "   public Mod(IEventBus modBus) {\n      packetHandler.initialize();\n   }\n}\n", encoding="utf-8")
        _r, f5, _c = run(d, "m")
        h5, b5, m5 = ((q / "Handler.java").read_text(encoding="utf-8"), (q / "Base.java").read_text(encoding="utf-8"),
                      (pathlib.Path(d) / "m/Mod.java").read_text(encoding="utf-8"))
        ok5 = ("public static void initialize(RegisterPayloadHandlersEvent event)" in h5
               and "registrar.playToClient(P.TYPE, P.STREAM_CODEC, P::handle);" in h5 and "getChannel" not in h5
               and "INSTANCE" not in h5 and "modBus.addListener(Handler::initialize);" in m5
               and "registerServerToClient" not in b5 and "getChannel" not in b5 and "createChannel" not in b5
               and "PacketDistributor.sendToAllPlayers(message);" in b5 and "getPlayer(IPayloadContext context)" in b5
               and "<MSG extends CustomPacketPayload> void sendToAll" in b5)
        if not ok5:
            print("self-check X154:", f5, "\n" + h5 + b5 + m5)
        ok = ok and ok5
    print("self-check:", "OK" if ok else f"FAIL {miss} {flags}\n{net}\n{pong}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
