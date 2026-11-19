#!/usr/bin/env python3
"""零跑方控补丁注入器（songloft-player-lynx 版）。

设计原则与 Flutter 版一致：不改上游源码，构建期文本注入，每次同步上游后可重复执行。

三种模式：
  --apply-source    复制 5 个 Kotlin 文件 + 在 AndroidManifest 注册静态接收器/初始化 Provider
  --apply-workflow  在 build-and-release.yml 的 android job 插入 --apply-source 调用步骤
  --check           只校验锚点与依赖项，不写任何文件

默认（不带参数）等价于 --apply-source --apply-workflow。

任何锚点缺失或命中多处都会 `::error::` 并 exit 1，绝不静默放行——否则构建出的
APK 看起来正常，方控却悄悄失效。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PATCH_DIR = Path(__file__).resolve().parent
ROOT = PATCH_DIR.parent

KOTLIN_DIR = ROOT / "android" / "app" / "src" / "main" / "java" / "org" / "songloft" / "lynx"
MANIFEST = ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
WORKFLOW = ROOT / ".github" / "workflows" / "build-and-release.yml"

KT_FILES = (
    "Leapmotor24Wheel.kt",
    "LeapmotorMediaClaim.kt",
    "Leapmotor24SteeringReceiver.kt",
    "Leapmotor24ConfigReceiver.kt",
    "LeapmotorInitProvider.kt",
)

MANIFEST_BEGIN = "<!-- BEGIN leapmotor24 steering patch (injected) -->"
MANIFEST_END = "<!-- END leapmotor24 steering patch -->"

# @@ 会被替换为相对 </application> 的子级缩进
MANIFEST_BLOCK = """@@BEGIN
@@<receiver
@@    android:name=".Leapmotor24SteeringReceiver"
@@    android:exported="true">
@@    <intent-filter>
@@        <action android:name="com.leapmotor.steerwheelcontrol.media" />
@@        <action android:name="org.songloft.lynx.leapmotor24.CONFIGURE" />
@@    </intent-filter>
@@</receiver>
@@<provider
@@    android:name=".LeapmotorInitProvider"
@@    android:authorities="${applicationId}.leapmotor24.init"
@@    android:exported="false" />
@@END""".replace("BEGIN", MANIFEST_BEGIN).replace("END", MANIFEST_END)

WORKFLOW_ANCHOR = "      - run: node scripts/copy-bundle-android.mjs"
WORKFLOW_STEP_NAME = "Inject Leapmotor steering patch"
WORKFLOW_STEP = (
    f"      - name: {WORKFLOW_STEP_NAME}\n"
    "        run: python3 patch/inject_leapmotor.py --apply-source"
)


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(1)


def read_text(path: Path) -> str:
    # newline='' 保留原文件换行风格，避免整文件被判定重写
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def check_sources() -> None:
    for name in KT_FILES:
        if not (PATCH_DIR / name).is_file():
            fail(f"补丁源文件缺失: patch/{name}")
    if not MANIFEST.is_file():
        fail(f"AndroidManifest 不存在: {MANIFEST}")
    if not WORKFLOW.is_file():
        fail(f"构建工作流不存在: {WORKFLOW}")


def apply_source(check_only: bool = False) -> None:
    """复制 Kotlin 源码 + 注册 Manifest 组件。"""
    for name in KT_FILES:
        src = PATCH_DIR / name
        dst = KOTLIN_DIR / name
        if not src.is_file():
            fail(f"补丁源文件缺失: patch/{name}")
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            print(f"[unchanged] android/.../{name}")
        elif not check_only:
            KOTLIN_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"[installed] android/.../{name}")
        else:
            print(f"[would install] android/.../{name}")

    manifest = read_text(MANIFEST)
    if MANIFEST_BEGIN in manifest or MANIFEST_END in manifest:
        print("[unchanged] AndroidManifest.xml 已注入")
        return

    matches = list(re.finditer(r"^([ \t]*)</application>", manifest, re.MULTILINE))
    if len(matches) != 1:
        fail(f"AndroidManifest 的 </application> 锚点应恰好 1 处，实际 {len(matches)} 处")
    indent = matches[0].group(1)
    child_indent = indent + "    "
    block = "\n".join(
        line.replace("@@", child_indent, 1) if line.startswith("@@") else line
        for line in MANIFEST_BLOCK.splitlines()
    )
    insertion = block + "\n" + indent + "</application>"
    updated = manifest[: matches[0].start()] + insertion + manifest[matches[0].end() :]
    if check_only:
        print("[would patch] AndroidManifest.xml")
    else:
        write_text(MANIFEST, updated)
        print("[patched] AndroidManifest.xml（静态接收器 + 初始化 Provider）")


def apply_workflow(check_only: bool = False) -> None:
    """在 android job 的 bundle 拷贝后插入补丁步骤。"""
    workflow = read_text(WORKFLOW)
    if WORKFLOW_STEP_NAME in workflow:
        print("[unchanged] build-and-release.yml 已注入")
        return
    count = workflow.count(WORKFLOW_ANCHOR)
    if count != 1:
        fail(f"build-and-release.yml 的 copy-bundle 锚点应恰好 1 处，实际 {count} 处")
    updated = workflow.replace(WORKFLOW_ANCHOR, WORKFLOW_ANCHOR + "\n" + WORKFLOW_STEP, 1)
    if check_only:
        print("[would patch] build-and-release.yml")
    else:
        write_text(WORKFLOW, updated)
        print("[patched] build-and-release.yml（android job 注入步骤）")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject Leapmotor steering patch")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply-source", action="store_true", help="只注入 Kotlin + Manifest")
    group.add_argument("--apply-workflow", action="store_true", help="只注入构建步骤")
    group.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = parser.parse_args()

    check_sources()
    check_only = args.check
    if args.apply_source:
        apply_source(check_only)
    elif args.apply_workflow:
        apply_workflow(check_only)
    else:
        apply_source(check_only)
        apply_workflow(check_only)


if __name__ == "__main__":
    main()
