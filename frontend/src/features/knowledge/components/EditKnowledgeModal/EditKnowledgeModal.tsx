/**
 * 编辑知识库模态框组件
 */
import React, { useEffect } from 'react';
import { X } from 'lucide-react';
import { KNOWLEDGE_CATEGORIES } from '@/constants/categories';
import type { KnowledgeBaseFormData } from '@/features/knowledge/types/forms';
import {
  isKnowledgeDescriptionWithinLimit,
  isKnowledgeNameWithinLimit,
} from '@/features/knowledge/utils/formValidation';
import { useToast } from '@/shared/hooks/useToast';
import styles from './EditKnowledgeModal.module.css';

interface EditKnowledgeModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (data: KnowledgeBaseFormData) => void;
  initialData: KnowledgeBaseFormData;
}

export default function EditKnowledgeModal({
  isOpen,
  onClose,
  onSave,
  initialData
}: EditKnowledgeModalProps) {
  const toast = useToast();
  const [name, setName] = React.useState(initialData.name);
  const [description, setDescription] = React.useState(initialData.description);
  const [category, setCategory] = React.useState(initialData.category || '其它');

  useEffect(() => {
    if (isOpen) {
      setName(initialData.name);
      setDescription(initialData.description);
      setCategory(initialData.category || '其它');
    }
  }, [isOpen, initialData]);

  if (!isOpen) return null;

  const handleNameChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    if (isKnowledgeNameWithinLimit(value)) {
      setName(value);
    }
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();

    if (!name.trim()) {
      toast.warning('请输入知识库名称');
      return;
    }

    const trimmedName = name.trim();
    if (!isKnowledgeNameWithinLimit(trimmedName)) {
      toast.warning('知识库名称过长，最多8个汉字或16个字母');
      return;
    }

    const trimmedDescription = description.trim();
    if (!isKnowledgeDescriptionWithinLimit(trimmedDescription)) {
      toast.warning('知识库描述过长，最多30个汉字或60个字母');
      return;
    }

    onSave({
      name: trimmedName,
      description: trimmedDescription,
      category
    });
    onClose();
  };

  const handleClose = () => {
    onClose();
  };

  return (
    <div className={styles.overlay} onClick={handleClose}>
      <div className={styles.modal} onClick={(event) => event.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>编辑知识库</h2>
          <button className={styles.closeBtn} onClick={handleClose}>
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.field}>
            <label className={styles.label}>
              知识库名称
              <span className={styles.hint}>（最多8个汉字或16个字母）</span>
            </label>
            <input
              type="text"
              className={styles.input}
              value={name}
              onChange={handleNameChange}
              autoFocus
            />
          </div>

          <div className={styles.field}>
            <label className={styles.label}>
              概述
              <span className={styles.hint}>（最多30个汉字或60个字母）</span>
            </label>
            <textarea
              className={styles.textarea}
              value={description}
              onChange={(event) => {
                const value = event.target.value;
                if (isKnowledgeDescriptionWithinLimit(value)) {
                  setDescription(value);
                }
              }}
              rows={3}
            />
          </div>

          <div className={styles.field}>
            <label className={styles.label}>分类</label>
            <select
              className={styles.select}
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              {KNOWLEDGE_CATEGORIES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>

          <div className={styles.actions}>
            <button type="button" className={styles.cancelBtn} onClick={handleClose}>
              取消
            </button>
            <button type="submit" className={styles.saveBtn}>
              保存
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
