import React, { useState, useRef, useEffect } from 'react';
import { X, ChevronDown, Check } from 'lucide-react';
import { KNOWLEDGE_CATEGORIES, CATEGORY_ICONS, CATEGORY_COLORS } from '@/constants/categories';
import type { KnowledgeBaseFormData } from '@/features/knowledge/types/forms';
import {
  isKnowledgeDescriptionWithinLimit,
  isKnowledgeNameWithinLimit,
} from '@/features/knowledge/utils/formValidation';
import { useToast } from '@/shared/hooks/useToast';
import styles from './CreateKnowledgeModal.module.css';

interface CreateKnowledgeModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: KnowledgeBaseFormData) => void;
}

export default function CreateKnowledgeModal({ isOpen, onClose, onSubmit }: CreateKnowledgeModalProps) {
  const toast = useToast();
  const [name, setName] = React.useState('');
  const [description, setDescription] = React.useState('');
  const [category, setCategory] = React.useState('其它');
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);

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

    onSubmit({
      name: trimmedName,
      description: trimmedDescription,
      category
    });

    setName('');
    setDescription('');
    setCategory('其它');
    setIsDropdownOpen(false);
    onClose();
  };

  const handleClose = () => {
    setName('');
    setDescription('');
    setCategory('其它');
    setIsDropdownOpen(false);
    onClose();
  };

  const handleCategorySelect = (value: string) => {
    setCategory(value);
    setIsDropdownOpen(false);
  };

  if (!isOpen) return null;

  const SelectedIcon = CATEGORY_ICONS[category];

  return (
    <div className={styles.overlay} onClick={handleClose}>
      <div className={styles.modal} onClick={(event) => event.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>新建知识库</h2>
          <button className={styles.closeBtn} onClick={handleClose}>
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.field}>
            <label className={styles.label}>
              知识库名称 <span className={styles.required}>*</span>
              <span className={styles.hint}>（最多8个汉字或16个字母）</span>
            </label>
            <input
              type="text"
              className={styles.input}
              placeholder="输入知识库名称"
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
              placeholder="简单描述这个知识库的用途和内容"
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
            <label className={styles.label}>
              分类 <span className={styles.required}>*</span>
            </label>

            <div className={styles.selectWrapper} ref={dropdownRef}>
              <div
                className={`${styles.selectTrigger} ${isDropdownOpen ? styles.open : ''}`}
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
              >
                <div className={styles.selectedContent}>
                  {SelectedIcon && (
                    <SelectedIcon
                      size={18}
                      style={{ color: CATEGORY_COLORS[category] }}
                      className={styles.categoryIcon}
                    />
                  )}
                  <span>{category}</span>
                </div>
                <ChevronDown
                  size={16}
                  className={`${styles.chevron} ${isDropdownOpen ? styles.rotate : ''}`}
                />
              </div>

              {isDropdownOpen && (
                <div className={styles.selectDropdown}>
                  {KNOWLEDGE_CATEGORIES.map((item) => {
                    const Icon = CATEGORY_ICONS[item];
                    const isSelected = category === item;
                    return (
                      <div
                        key={item}
                        className={`${styles.selectOption} ${isSelected ? styles.selected : ''}`}
                        onClick={() => handleCategorySelect(item)}
                      >
                        <div className={styles.optionContent}>
                          {Icon ? (
                            <Icon
                              size={18}
                              style={{ color: CATEGORY_COLORS[item] }}
                              className={styles.categoryIcon}
                            />
                          ) : (
                            <div className={styles.categoryIconPlaceholder} />
                          )}
                          <span className={styles.optionLabel}>{item}</span>
                        </div>
                        {isSelected && <Check size={16} className={styles.checkIcon} />}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          <div className={styles.actions}>
            <button type="button" className={styles.cancelBtn} onClick={handleClose}>
              取消
            </button>
            <button type="submit" className={styles.submitBtn}>
              创建
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
