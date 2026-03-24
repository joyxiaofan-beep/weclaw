"""
人脉记忆系统 — ContactMemory
从对话中学习，逐步建立每个人的画像。
数据存储为 YAML 文件，人类可读、易于调试。
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger
from pydantic import BaseModel, Field


class Interaction(BaseModel):
    """一次交互记录"""
    time: str = Field(default_factory=lambda: datetime.now().isoformat())
    direction: str  # "outgoing" = 龙虾发给他 | "incoming" = 他回复龙虾
    summary: str     # 交互摘要
    topics: list[str] = Field(default_factory=list)  # 涉及的话题
    raw_content: Optional[str] = None  # 原始内容（可选保留）


class ContactProfile(BaseModel):
    """一个人的画像"""
    # 基本信息
    name: str                         # 称呼（你怎么叫他的）
    external_id: Optional[str] = None # 外部系统 ID
    real_name: Optional[str] = None   # 真名
    department: Optional[str] = None  # 部门
    title: Optional[str] = None       # 职级/角色

    # 龙虾学到的印象（AI 从交互中提炼）
    expertise: list[str] = Field(default_factory=list)   # 擅长领域
    traits: list[str] = Field(default_factory=list)      # 沟通特征
    notes: list[str] = Field(default_factory=list)       # 备注/印象

    # 交互历史
    interactions: list[Interaction] = Field(default_factory=list)

    # 统计
    first_contact: Optional[str] = None   # 首次接触时间
    last_contact: Optional[str] = None    # 最近接触时间
    total_interactions: int = 0           # 总交互次数

    # AI 生成的综合画像摘要（定期更新）
    ai_summary: Optional[str] = None


class ContactMemory:
    """
    人脉记忆管理器

    负责：
    1. 存取联系人画像
    2. 记录每次交互
    3. 根据话题/姓名查找合适的人
    """

    def __init__(self, data_dir: str = "data/contacts"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._contacts: dict[str, ContactProfile] = {}
        self._load_all()

    def _load_all(self):
        """加载所有联系人画像"""
        for f in self.data_dir.glob("*.yaml"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = yaml.safe_load(fp)
                    if data:
                        profile = ContactProfile(**data)
                        self._contacts[profile.name] = profile
            except Exception as e:
                logger.warning(f"加载联系人失败 {f}: {e}")

        logger.info(f"已加载 {len(self._contacts)} 个联系人画像")

    def _save_contact(self, profile: ContactProfile):
        """保存单个联系人画像"""
        filename = self.data_dir / f"{profile.name}.yaml"
        with open(filename, "w", encoding="utf-8") as fp:
            yaml.dump(
                profile.model_dump(),
                fp,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )

    # ──────────────────────────────────────────
    # 联系人管理
    # ──────────────────────────────────────────

    def get_contact(self, name: str) -> Optional[ContactProfile]:
        """获取联系人画像"""
        return self._contacts.get(name)

    def get_or_create_contact(self, name: str, **kwargs) -> ContactProfile:
        """获取或创建联系人"""
        if name in self._contacts:
            return self._contacts[name]

        profile = ContactProfile(
            name=name,
            first_contact=datetime.now().isoformat(),
            **kwargs,
        )
        self._contacts[name] = profile
        self._save_contact(profile)
        logger.info(f"新建联系人画像: {name}")
        return profile

    def list_contacts(self) -> list[ContactProfile]:
        """列出所有联系人"""
        return list(self._contacts.values())

    # ──────────────────────────────────────────
    # 交互记录
    # ──────────────────────────────────────────

    def record_interaction(
        self,
        name: str,
        direction: str,
        summary: str,
        topics: list[str] = None,
        raw_content: str = None,
    ):
        """
        记录一次交互

        Args:
            name: 联系人称呼
            direction: "outgoing" 或 "incoming"
            summary: 交互摘要
            topics: 涉及的话题
            raw_content: 原始内容（可选）
        """
        profile = self.get_or_create_contact(name)

        interaction = Interaction(
            direction=direction,
            summary=summary,
            topics=topics or [],
            raw_content=raw_content,
        )

        profile.interactions.append(interaction)
        profile.last_contact = interaction.time
        profile.total_interactions += 1

        # 更新擅长领域（简单累加，后续 AI 会精炼）
        for topic in (topics or []):
            if topic not in profile.expertise:
                profile.expertise.append(topic)

        self._save_contact(profile)
        logger.info(f"记录交互: {direction} {name} (topics: {len(topics or [])})")

    # ──────────────────────────────────────────
    # 智能查找
    # ──────────────────────────────────────────

    def find_by_topic(self, topic: str) -> list[tuple[ContactProfile, int]]:
        """
        根据话题查找相关联系人

        Args:
            topic: 话题关键词

        Returns:
            [(联系人画像, 相关度得分)] 按得分降序排列
        """
        results = []
        topic_lower = topic.lower()

        for profile in self._contacts.values():
            score = 0

            # 擅长领域匹配
            for exp in profile.expertise:
                if topic_lower in exp.lower():
                    score += 10

            # 交互话题匹配
            for interaction in profile.interactions:
                for t in interaction.topics:
                    if topic_lower in t.lower():
                        score += 2

            # 备注匹配
            for note in profile.notes:
                if topic_lower in note.lower():
                    score += 5

            if score > 0:
                results.append((profile, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def find_by_name(self, name: str) -> Optional[ContactProfile]:
        """
        模糊姓名查找

        Args:
            name: 姓名（支持部分匹配）
        """
        # 精确匹配
        if name in self._contacts:
            return self._contacts[name]

        # 模糊匹配
        for key, profile in self._contacts.items():
            if name in key or (profile.real_name and name in profile.real_name):
                return profile

        return None

    # ──────────────────────────────────────────
    # AI Summary 管理
    # ──────────────────────────────────────────

    def update_ai_summary(self, name: str, summary: str) -> bool:
        """
        更新联系人的 AI 综合画像摘要

        Args:
            name: 联系人名称
            summary: AI 生成的画像摘要

        Returns:
            是否成功更新
        """
        profile = self.get_contact(name)
        if not profile:
            return False

        profile.ai_summary = summary
        self._save_contact(profile)
        logger.info(f"AI Summary 已更新: {name}")
        return True

    def should_refresh_summary(self, name: str, interval: int = 10, min_interactions: int = 3) -> bool:
        """
        判断是否应该刷新某联系人的 AI Summary

        触发条件（满足任一）：
        1. 从未生成过 + 交互次数 >= min_interactions
        2. 交互次数是 interval 的整数倍（每 N 次交互刷新一次）

        Args:
            name: 联系人名称
            interval: 每隔多少次交互刷新一次
            min_interactions: 首次生成的最低交互次数

        Returns:
            是否应该刷新
        """
        profile = self.get_contact(name)
        if not profile:
            return False

        total = profile.total_interactions

        # 从未生成过，且达到最低交互阈值
        if profile.ai_summary is None and total >= min_interactions:
            return True

        # 已有 Summary，检查是否到了刷新周期
        if profile.ai_summary is not None and total > 0 and total % interval == 0:
            return True

        return False

    # ──────────────────────────────────────────
    # 画像输出（给 AI 看的）
    # ──────────────────────────────────────────

    def get_contact_brief(self, name: str) -> str:
        """获取联系人的简要画像描述（用于注入 AI prompt）"""
        profile = self.get_contact(name)
        if not profile:
            return f"我不认识 {name}，这是一个新联系人。"

        lines = [f"## {profile.name}"]

        if profile.real_name:
            lines.append(f"真名: {profile.real_name}")
        if profile.department:
            lines.append(f"部门: {profile.department}")
        if profile.title:
            lines.append(f"职级: {profile.title}")
        if profile.expertise:
            lines.append(f"擅长: {', '.join(profile.expertise)}")
        if profile.traits:
            lines.append(f"沟通特征: {', '.join(profile.traits)}")
        if profile.notes:
            lines.append(f"备注: {'; '.join(profile.notes)}")
        if profile.ai_summary:
            lines.append(f"综合印象: {profile.ai_summary}")

        lines.append(f"总共交互 {profile.total_interactions} 次")
        if profile.last_contact:
            lines.append(f"最近联系: {profile.last_contact[:10]}")

        # 最近 5 次交互
        recent = profile.interactions[-5:]
        if recent:
            lines.append("\n最近交互:")
            for i in recent:
                arrow = "→" if i.direction == "outgoing" else "←"
                lines.append(f"  {arrow} [{i.time[:10]}] {i.summary}")

        return "\n".join(lines)

    def get_all_contacts_brief(self) -> str:
        """获取所有联系人的概要（用于 AI 决策）"""
        if not self._contacts:
            return "通讯录为空，还没有认识任何人。"

        lines = ["# 我的人脉网络\n"]
        for profile in self._contacts.values():
            expertise_str = ", ".join(profile.expertise[:3]) if profile.expertise else "未知"
            lines.append(
                f"- **{profile.name}**: {expertise_str} "
                f"(交互{profile.total_interactions}次)"
            )

        return "\n".join(lines)

    # ──────────────────────────────────────────
    # Web 管理接口
    # ──────────────────────────────────────────

    def update_contact(self, name: str, updates: dict) -> Optional[ContactProfile]:
        """
        更新联系人信息（Web 管理用）

        Args:
            name: 联系人名称
            updates: 要更新的字段 dict

        Returns:
            更新后的 profile，不存在则返回 None
        """
        profile = self.get_contact(name)
        if not profile:
            return None

        # 允许更新的字段
        editable_fields = {
            "real_name", "department", "title", "external_id",
            "expertise", "traits", "notes", "ai_summary",
        }

        for key, value in updates.items():
            if key in editable_fields and hasattr(profile, key):
                setattr(profile, key, value)

        # 如果改了名字，需要重新映射
        new_name = updates.get("name")
        if new_name and new_name != name:
            old_file = self.data_dir / f"{name}.yaml"
            if old_file.exists():
                old_file.unlink()
            del self._contacts[name]
            profile.name = new_name
            self._contacts[new_name] = profile

        self._save_contact(profile)
        logger.info(f"更新联系人: {profile.name} (fields: {list(updates.keys())})")
        return profile

    def delete_contact(self, name: str) -> bool:
        """
        删除联系人

        Args:
            name: 联系人名称

        Returns:
            是否成功删除
        """
        if name not in self._contacts:
            return False

        filepath = self.data_dir / f"{name}.yaml"
        if filepath.exists():
            filepath.unlink()

        del self._contacts[name]
        logger.info(f"删除联系人: {name}")
        return True

    def merge_contacts(self, keep_name: str, merge_name: str) -> Optional[ContactProfile]:
        """
        合并两个联系人（将 merge_name 的数据合入 keep_name）

        Args:
            keep_name: 保留的联系人
            merge_name: 被合并的联系人（合并后删除）

        Returns:
            合并后的 profile，失败返回 None
        """
        keep = self.get_contact(keep_name)
        merge = self.get_contact(merge_name)

        if not keep or not merge:
            return None

        # 合并基本信息（merge 的信息补充 keep 的空缺）
        if not keep.real_name and merge.real_name:
            keep.real_name = merge.real_name
        if not keep.department and merge.department:
            keep.department = merge.department
        if not keep.title and merge.title:
            keep.title = merge.title
        if not keep.external_id and merge.external_id:
            keep.external_id = merge.external_id

        # 合并列表（去重）
        for exp in merge.expertise:
            if exp not in keep.expertise:
                keep.expertise.append(exp)
        for trait in merge.traits:
            if trait not in keep.traits:
                keep.traits.append(trait)
        for note in merge.notes:
            if note not in keep.notes:
                keep.notes.append(note)

        # 合并交互历史（按时间排序）
        keep.interactions.extend(merge.interactions)
        keep.interactions.sort(key=lambda x: x.time)
        keep.total_interactions = len(keep.interactions)

        # 更新时间
        if merge.first_contact and (
            not keep.first_contact or merge.first_contact < keep.first_contact
        ):
            keep.first_contact = merge.first_contact
        if merge.last_contact and (
            not keep.last_contact or merge.last_contact > keep.last_contact
        ):
            keep.last_contact = merge.last_contact

        # 保存合并后的结果 & 删除被合并的
        self._save_contact(keep)
        self.delete_contact(merge_name)

        logger.info(f"合并联系人: {merge_name} → {keep_name}")
        return keep

    def to_dict_list(self) -> list[dict]:
        """将所有联系人导出为 dict 列表（供 API 返回）"""
        result = []
        for profile in self._contacts.values():
            d = profile.model_dump()
            # 简化交互历史（只返回最近 10 条）
            d["interactions"] = d["interactions"][-10:]
            result.append(d)
        return result
