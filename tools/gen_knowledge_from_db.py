#!/usr/bin/env python3
"""从 STM32CubeMX 安装目录的 db/ 导出知识库(只读 CubeMX,只写 knowledge/)。

事实来源是 CubeMX 自己的数据文件,不是人的记忆:
  db/mcu/<Mcu.Name>.xml                 芯片:引脚、每个引脚可选的信号、IP 列表与版本
  db/mcu/IP/<Name>-<Version>_Modes.xml  IP:模式名、参数名与取值范围、信号组名

芯片用模板 .ioc 里的 Mcu.Name 定位(它和 db/mcu 下的 XML 文件名一致);
IP 用 mcu XML 里的 Name + Version 拼出文件名。

用法:
  python tools/gen_knowledge_from_db.py
  python tools/gen_knowledge_from_db.py --mcu STM32F103C8T6 --out knowledge
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MCU = "STM32F103C8T6"
EXE_NAME = "STM32CubeMX.exe"


def local_name(tag: str) -> str:
    """去掉 XML 命名空间,只留标签名。"""
    return tag.rsplit("}", 1)[-1]


def find_db_dir(cubemx_exe: str = "") -> Path:
    """定位 CubeMX 的 db 目录:显式路径 -> 环境变量 ST_CUBEMX_EXE -> PATH -> 常见安装位置。"""
    tried = []
    for raw in (cubemx_exe, os.environ.get("ST_CUBEMX_EXE", "")):
        if raw and raw.strip():
            exe = Path(raw.strip())
            tried.append(str(exe))
            db = exe.parent / "db"
            if db.is_dir():
                return db
            raise SystemExit(f"{exe} 旁边没有 db 目录(找的是 {db});请检查 --cubemx-exe / ST_CUBEMX_EXE")
    which = shutil.which("STM32CubeMX")
    if which:
        tried.append(which)
        db = Path(which).parent / "db"
        if db.is_dir():
            return db
    for guess in ("C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeMX", "C:/ST/STM32CubeMX"):
        db = Path(guess) / "db"
        tried.append(guess)
        if db.is_dir():
            return db
    raise SystemExit("找不到 CubeMX 的 db 目录。已尝试: " + (", ".join(tried) or "(无)") + ";请用 --cubemx-exe 指定")


def read_template_identity(template: Path) -> dict:
    """从模板 .ioc 里读芯片标识(Mcu.Name 用于定位 mcu XML)。"""
    wanted = ("Mcu.CPN", "Mcu.Name", "Mcu.Family", "Mcu.Package", "MxCube.Version", "MxDb.Version")
    out = {}
    for line in template.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in wanted:
            out[key] = value.strip()
    if not out.get("Mcu.Name"):
        raise SystemExit(f"{template} 里没有 Mcu.Name,无法定位芯片 XML")
    return out


def parse_mcu_xml(path: Path) -> dict:
    """解析芯片 XML:芯片属性 + IP 列表 + 引脚(含每个引脚可选信号)。"""
    root = ET.parse(path).getroot()
    chip = {
        "ref_name": root.get("RefName", ""),
        "family": root.get("Family", ""),
        "line": root.get("Line", ""),
        "package": root.get("Package", ""),
    }
    flashes = []
    ips = []
    pins = {}
    for el in root:
        tag = local_name(el.tag)
        if tag == "Core":
            chip["core"] = (el.text or "").strip()
        elif tag == "Frequency":
            chip["frequency_mhz"] = (el.text or "").strip()
        elif tag == "Ram":
            chip["ram_kb"] = (el.text or "").strip()
        elif tag == "Flash":
            flashes.append((el.text or "").strip())
        elif tag == "IONb":
            chip["io_count"] = (el.text or "").strip()
        elif tag == "Die":
            chip["die"] = (el.text or "").strip()
        elif tag == "IP":
            item = {"instance": el.get("InstanceName", ""), "name": el.get("Name", "")}
            for attr, key in (
                ("Version", "version"),
                ("ConfigFile", "config_file"),
                ("ClockEnableMode", "clock_enable_mode"),
            ):
                value = el.get(attr, "")
                if value:
                    item[key] = value
            ips.append(item)
        elif tag == "Pin":
            entry = {"position": el.get("Position", ""), "type": el.get("Type", "")}
            signals = []
            for sig in el:
                if local_name(sig.tag) != "Signal":
                    continue
                signals.append(sig.get("Name", ""))
                if sig.get("IOModes"):
                    entry["gpio_io_modes"] = sig.get("IOModes")
            entry["signals"] = signals
            pins[el.get("Name", "")] = entry
    if flashes:
        chip["flash_kb"] = flashes
    return {"chip": chip, "ips": ips, "pins": pins}


def parse_modes_xml(path: Path) -> dict:
    """解析 IP 的 Modes XML:参数、信号组名、模式(含每个模式用到的参数)。"""
    root = ET.parse(path).getroot()
    data = {
        "name": root.get("Name", ""),
        "version": root.get("Version", ""),
        "parameters": {},
        "signals": {},
        "modes": [],
    }
    for el in root.iter():
        tag = local_name(el.tag)
        if tag == "RefParameter":
            name = el.get("Name", "")
            if not name:
                continue
            # 同名参数在 XML 里可能有多份定义(其中一份是 Visible=false 的精简副本,
            # 坑:之前 NbrOfConversion 的 max 就被它带偏过)。所以属性和取值域都要**合并**:
            # 属性取先出现的非空值,values 取并集 —— 只取第一份会丢掉大部分合法值。
            item = data["parameters"].get(name)
            if item is None:
                item = {}
                data["parameters"][name] = item
            for attr, key in (
                ("Comment", "comment"),
                ("Type", "type"),
                ("DefaultValue", "default"),
                ("Min", "min"),
                ("Max", "max"),
                ("Unit", "unit"),
                ("Group", "group"),
            ):
                value = el.get(attr, "")
                if value and not item.get(key):
                    item[key] = value
            possible = [pv.get("Value") for pv in el if local_name(pv.tag) == "PossibleValue" and pv.get("Value")]
            if possible:
                merged = list(item.get("values") or [])
                merged += [v for v in possible if v not in merged]
                item["values"] = merged
        elif tag == "RefSignal":
            name = el.get("Name", "")
            if not name or name in data["signals"]:
                continue
            item = {}
            for attr, key in (
                ("IOMode", "io_mode"),
                ("Direction", "direction"),
                ("ExclusiveGroupName", "exclusive_group"),
                ("ShareableGroupName", "shareable_group"),
            ):
                value = el.get(attr, "")
                if value:
                    item[key] = value
            data["signals"][name] = item
        elif tag == "RefMode":
            params = []
            possible = {}
            for child in el:
                if local_name(child.tag) != "Parameter":
                    continue
                pname = child.get("Name", "")
                if not pname:
                    continue
                params.append(pname)
                values = [pv.text.strip() for pv in child if local_name(pv.tag) == "PossibleValue" and pv.text]
                if values:
                    possible[pname] = values
            mode = {"name": el.get("Name", ""), "hal_mode": el.get("HalMode", "")}
            if el.get("BaseMode"):
                mode["base_mode"] = el.get("BaseMode")
            if el.get("Group"):
                mode["group"] = el.get("Group")
            mode["parameters"] = params
            if possible:
                mode["possible_values"] = possible
            data["modes"].append(mode)
    return data


def find_ip_xml(ip_dir: Path, name: str, version: str) -> Path | None:
    """按 <Name>-<Version>_Modes.xml 精确定位;找不到就退而求其次匹配前缀,歧义则报错。"""
    exact = ip_dir / f"{name}-{version}_Modes.xml"
    if exact.is_file():
        return exact
    candidates = sorted(ip_dir.glob(f"{name}-*_Modes.xml"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        picked = [c for c in candidates if version and version.split("_")[0] in c.name]
        if len(picked) == 1:
            return picked[0]
        raise SystemExit(f"{name} 的 Modes XML 有多个候选,无法确定: {[c.name for c in candidates]}")
    return None


def parse_dma_requests(path: Path) -> dict:
    """解析 DMA 的 Modes XML,返回 {"channels": {请求: 通道}, "directions": {请求: 方向}}。

    通道藏在 ModeLogicOperator 树里(不是 config/DMA-*_Configs.xml —— 那个只有 HAL 函数定义):
      IP[DMA] -> ModeLogicOperator[OR] -> Mode[DMA1] -> ModeLogicOperator[OR]
        -> Mode[DMA1_Channel1] -> ModeLogicOperator[XOR] -> Mode[<请求名>]

    方向来自同一文件里 RefMode(BaseMode="DMA_Request") 的 Direction 参数;取值不唯一时不写
    (调用方回落到 rules/dma.yaml 的默认值)。MEMTOMEM(内存到内存)不是外设请求,跳过。
    同一请求映射到多个通道时收成列表(F1 上每个请求只有一个固定通道)。
    """
    root = ET.parse(path).getroot()
    found: dict = {}
    directions: dict = {}
    for el in root.iter():
        tag = local_name(el.tag)
        if tag == "RefMode" and el.get("BaseMode") == "DMA_Request":
            name = el.get("Name", "")
            values = []
            for param in el:
                if local_name(param.tag) != "Parameter" or param.get("Name") != "Direction":
                    continue
                values = [pv.text.strip() for pv in param if local_name(pv.tag) == "PossibleValue" and pv.text]
            if name and len(set(values)) == 1:
                directions[name] = values[0]
        elif tag == "Mode":
            dma_name = el.get("Name", "")
            if not dma_name.startswith("DMA"):
                continue
            for group in [c for c in el if local_name(c.tag) == "ModeLogicOperator"]:
                for channel in [c for c in group if local_name(c.tag) == "Mode"]:
                    channel_name = channel.get("Name", "")
                    if not channel_name.startswith(f"{dma_name}_Channel"):
                        continue
                    for sub in [c for c in channel if local_name(c.tag) == "ModeLogicOperator"]:
                        for request in [c for c in sub if local_name(c.tag) == "Mode"]:
                            name = request.get("Name", "")
                            if not name or name == "MEMTOMEM":
                                continue
                            found.setdefault(name, []).append(channel_name)
    return {
        "channels": {k: (v[0] if len(v) == 1 else sorted(set(v))) for k, v in found.items()},
        "directions": directions,
    }


def dump_yaml(path: Path, data: dict) -> None:
    """写 YAML(保留键顺序、不转义中文)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=200)


def main() -> None:
    parser = argparse.ArgumentParser(description="从 STM32CubeMX 的 db/ 导出知识库(只读 CubeMX,只写 knowledge/)")
    parser.add_argument(
        "--mcu", default=DEFAULT_MCU, help=f"芯片型号,用于在 templates/ 找模板 .ioc(默认 {DEFAULT_MCU})"
    )
    parser.add_argument("--template", default="", help="直接指定模板 .ioc 路径(优先于 --mcu)")
    parser.add_argument("--cubemx-exe", default="", help="STM32CubeMX.exe 路径;不传则读 ST_CUBEMX_EXE")
    parser.add_argument("--out", default="", help="知识库输出目录(默认 <项目根>/knowledge)")
    args = parser.parse_args()

    db_dir = find_db_dir(args.cubemx_exe)
    out_dir = Path(args.out).expanduser() if args.out else ROOT / "knowledge"
    template = Path(args.template) if args.template else ROOT / "templates" / f"{args.mcu}.ioc"
    if not template.is_file():
        raise SystemExit(f"找不到模板 {template};用 --template 指定,或先准备该芯片的模板")

    ident = read_template_identity(template)
    mcu_name = ident["Mcu.Name"]
    mcu_xml = db_dir / "mcu" / f"{mcu_name}.xml"
    if not mcu_xml.is_file():
        raise SystemExit(f"找不到芯片 XML: {mcu_xml}")

    mcu_data = parse_mcu_xml(mcu_xml)
    chip = {"cpn": ident.get("Mcu.CPN", ""), **mcu_data["chip"]}
    dump_yaml(
        out_dir / "mcu" / f"{mcu_name}.pins.yaml",
        {"chip": chip, "ips": mcu_data["ips"], "pins": mcu_data["pins"]},
    )
    print(f"芯片 {mcu_name}: {len(mcu_data['pins'])} 个引脚, {len(mcu_data['ips'])} 个 IP -> {out_dir / 'mcu'}")

    ip_dir = db_dir / "mcu" / "IP"
    sources = {}
    missing = []
    for ip in mcu_data["ips"]:
        xml = find_ip_xml(ip_dir, ip["name"], ip.get("version", ""))
        if xml is None:
            missing.append(ip["instance"])
            continue
        data = parse_modes_xml(xml)
        data["source"] = f"db/mcu/IP/{xml.name}"
        data["instance"] = ip["instance"]
        dump_yaml(out_dir / "ip" / f"{xml.stem}.yaml", data)
        sources[ip["instance"]] = xml.name
        print(f"  {ip['instance']:<12} -> {xml.name}  ({len(data['modes'])} 模式, {len(data['parameters'])} 参数)")

    # DMA 请求 -> 通道/方向(db 的 ModeLogicOperator 树;cubemx_intent 的 dma 意图用它查表)
    dma_ip = next((ip for ip in mcu_data["ips"] if ip["name"] == "DMA"), None)
    if dma_ip:
        dma_xml = find_ip_xml(ip_dir, dma_ip["name"], dma_ip.get("version", ""))
        if dma_xml:
            parsed = parse_dma_requests(dma_xml)
            dump_yaml(
                out_dir / "mcu" / f"{mcu_name}.dma.yaml",
                {
                    "mcu": mcu_name,
                    "cpn": ident.get("Mcu.CPN", ""),
                    "source": f"db/mcu/IP/{dma_xml.name}",
                    "channels_nb": len(parsed["channels"]),
                    "channels": parsed["channels"],
                    "directions": parsed["directions"],
                },
            )
            print(
                f"DMA 请求->通道: {len(parsed['channels'])} 条"
                f"(另有 {len(parsed['directions'])} 条带 db 默认方向)"
                f" -> {out_dir / 'mcu' / f'{mcu_name}.dma.yaml'}"
            )
        else:
            print(f"  (找不到 DMA 的 Modes XML,跳过通道表: {dma_ip['name']})")

    dump_yaml(
        out_dir / "meta.yaml",
        {
            "generated_by": "tools/gen_knowledge_from_db.py",
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "cubemx_dir": str(db_dir.parent).replace("\\", "/"),
            "db_dir": str(db_dir).replace("\\", "/"),
            "mxcube_version": ident.get("MxCube.Version", ""),
            "mxdb_version": ident.get("MxDb.Version", ""),
            "mcu": args.mcu,
            "mcu_name": mcu_name,
            "source_mcu_xml": f"db/mcu/{mcu_xml.name}",
            "source_ip_xml": sources,
            "no_modes_xml": missing,
        },
    )
    print(f"写出 {out_dir},其中没有 Modes XML 的 IP: {missing or '(无)'}")


if __name__ == "__main__":
    main()
