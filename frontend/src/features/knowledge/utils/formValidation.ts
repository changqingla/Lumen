const DOUBLE_WIDTH_CHAR_MAX = 16;
const DESCRIPTION_MAX = 60;

export const KNOWLEDGE_NAME_MAX_LENGTH = DOUBLE_WIDTH_CHAR_MAX;
export const KNOWLEDGE_DESCRIPTION_MAX_LENGTH = DESCRIPTION_MAX;

export const getKnowledgeTextLength = (value: string): number => {
  let length = 0;
  for (let index = 0; index < value.length; index += 1) {
    const charCode = value.charCodeAt(index);
    if (charCode >= 0x4e00 && charCode <= 0x9fff) {
      length += 2;
      continue;
    }
    length += 1;
  }
  return length;
};

export const isKnowledgeNameWithinLimit = (value: string): boolean =>
  getKnowledgeTextLength(value) <= KNOWLEDGE_NAME_MAX_LENGTH;

export const isKnowledgeDescriptionWithinLimit = (value: string): boolean =>
  getKnowledgeTextLength(value) <= KNOWLEDGE_DESCRIPTION_MAX_LENGTH;
