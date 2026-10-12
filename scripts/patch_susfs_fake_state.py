#!/usr/bin/env python3
import os
import sys

def patch_ksu(ksu_dir):
    # 1. Strip upstream build block in susfs_compat.mk if present
    compat_mk = os.path.join(ksu_dir, "kernel", "tools", "susfs_compat.mk")
    if os.path.isfile(compat_mk):
        with open(compat_mk, "r", encoding="utf-8") as f:
            mk_content = f.read()
        if "Please wait susfs update" in mk_content:
            print("[INFO] 移除 BakaSU 上游硬编码的 SUSFS 熔断阻断 (Please wait susfs update)...")
            lines = [line for line in mk_content.splitlines(keepends=True) if "Please wait susfs update" not in line]
            with open(compat_mk, "w", encoding="utf-8") as f:
                f.writelines(lines)

    # 2. Check selinux_hide.c
    selinux_hide_c = os.path.join(ksu_dir, "kernel", "feature", "selinux_hide.c")
    if not os.path.isfile(selinux_hide_c):
        print(f"[WARN] {selinux_hide_c} 不存在，跳过补丁。")
        return True

    with open(selinux_hide_c, "r", encoding="utf-8") as f:
        content = f.read()

    if "fake_state" in content:
        print("[INFO] KernelSU selinux_hide.c 已包含 fake_state 定义，无需补充补丁。")
        return True

    print("[INFO] 检测到 KernelSU 合并 PR 3877 后缺少 fake_state，正在为 SUSFS (<= 6.1) 注入兼容定义...")

    # 1. 注入 fake_state 符号声明
    decl_anchor = "__maybe_static bool ksu_selinux_hide_running __read_mostly = false;"
    if decl_anchor in content:
        decl_code = (
            decl_anchor
            + "\n#if defined(CONFIG_KSU_SUSFS) && (LINUX_VERSION_CODE < KERNEL_VERSION(6, 6, 0))\n"
            + "struct selinux_state fake_state;\n"
            + "#endif"
        )
        content = content.replace(decl_anchor, decl_code, 1)
    else:
        print(f"[ERROR] 未找到符号声明锚点: {decl_anchor}")
        return False

    # 2. 注入 ksu_selinux_hide_enable 初始化
    enable_anchor = 'if (!backup_sepolicy) {\n        pr_err("no backup sepolicy available, please save feature and reboot to retry!\\n");\n        return -EAGAIN;\n    }'
    if enable_anchor in content:
        enable_code = (
            enable_anchor
            + "\n#if defined(CONFIG_KSU_SUSFS) && (LINUX_VERSION_CODE < KERNEL_VERSION(6, 6, 0))\n"
            + "    fake_state.initialized = true;\n"
            + "    fake_state.policy = backup_sepolicy;\n"
            + "#endif"
        )
        content = content.replace(enable_anchor, enable_code, 1)
    else:
        # 兜底：尝试在 return 0 之前注入
        fallback_anchor = "return 0;\n\n#ifndef KSU_COMPAT_HAS_SUSFS_FEATURE_SELINUX_HIDE\nunhook:"
        if fallback_anchor in content:
            enable_code = (
                "#if defined(CONFIG_KSU_SUSFS) && (LINUX_VERSION_CODE < KERNEL_VERSION(6, 6, 0))\n"
                + "    fake_state.initialized = true;\n"
                + "    fake_state.policy = backup_sepolicy;\n"
                + "#endif\n    "
                + fallback_anchor
            )
            content = content.replace(fallback_anchor, enable_code, 1)
        else:
            print("[ERROR] 未能在 selinux_hide.c 找到 ksu_selinux_hide_enable 注入锚点")
            return False

    # 3. 注入 ksu_selinux_hide_disable 清理
    disable_anchor = 'pr_info("selinux_hide: exit selinux hide\\n");'
    if disable_anchor in content:
        disable_code = (
            disable_anchor
            + "\n#if defined(CONFIG_KSU_SUSFS) && (LINUX_VERSION_CODE < KERNEL_VERSION(6, 6, 0))\n"
            + "    fake_state.policy = NULL;\n"
            + "#endif"
        )
        content = content.replace(disable_anchor, disable_code, 1)

    # 4. 注入 ksu_selinux_hide_drop_backup_if_unused 清理
    drop_anchor = 'pr_info("selinux_hide is not enabled - drop backup_sepolicy\\n");'
    if drop_anchor in content:
        drop_code = (
            drop_anchor
            + "\n#if defined(CONFIG_KSU_SUSFS) && (LINUX_VERSION_CODE < KERNEL_VERSION(6, 6, 0))\n"
            + "        fake_state.policy = NULL;\n"
            + "#endif"
        )
        content = content.replace(drop_anchor, drop_code, 1)

    with open(selinux_hide_c, "w", encoding="utf-8") as f:
        f.write(content)

    print("[SUCCESS] 成功为 selinux_hide.c 注入 fake_state 兼容定义及生命周期管理！")
    return True

if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    success = patch_ksu(target_dir)
    sys.exit(0 if success else 1)
