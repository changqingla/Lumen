/**
 * 知识库分类常量
 * 包含所有预定义的学科分类及其图标和颜色配置
 */
import { Book, Briefcase, GraduationCap, FlaskConical, Palette, Scale, Heart, Brain, Atom, Dna, Landmark, Microscope } from 'lucide-react';

// 知识库分类列表
export const KNOWLEDGE_CATEGORIES = [
    "工学", "理学", "法学", "文学", "教育学", "经济学",
    "历史学", "哲学", "农学", "医学", "管理学", "艺术学", "其它"
];

// 分类图标映射
export const CATEGORY_ICONS: { [key: string]: any } = {
  "哲学": Book,
  "经济学": Briefcase,
  "法学": Scale,
  "教育学": GraduationCap,
  "文学": Palette,
  "历史学": Landmark,
  "理学": Atom,
  "工学": FlaskConical,
  "农学": Dna,
  "医学": Heart,
  "管理学": Brain,
  "艺术学": Palette,
  "其它": Microscope,
};

// 分类颜色映射
export const CATEGORY_COLORS: { [key: string]: string } = {
  "哲学": "#8B5CF6", // violet
  "经济学": "#F59E0B", // amber
  "法学": "#10B981", // emerald
  "教育学": "#3B82F6", // blue
  "文学": "#EC4899", // pink
  "历史学": "#EF4444", // red
  "理学": "#06B6D4", // cyan
  "工学": "#6366F1", // indigo
  "农学": "#22C55E", // green
  "医学": "#F43F5E", // rose
  "管理学": "#EAB308", // yellow
  "艺术学": "#D946EF", // fuchsia
  "其它": "#9CA3AF", // coolGray
};


