/**
 * 笔记相关工具函数
 * 处理对话保存到笔记的功能
 */

import { noteAPI } from '@/shared/api/client';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  createdAt?: string;
}

/**
 * 从消息列表中找到指定AI消息对应的用户问题
 * @param messages 消息列表
 * @param aiMessageIndex AI消息在列表中的索引
 * @returns 对应的用户消息，如果没找到返回null
 */
export function findUserQuestion(messages: Message[], aiMessageIndex: number): Message | null {
  // 从AI消息的前一条开始向前查找用户消息
  for (let i = aiMessageIndex - 1; i >= 0; i--) {
    if (messages[i].role === 'user') {
      return messages[i];
    }
  }
  return null;
}

/**
 * 生成笔记标题
 * 从用户问题中提取前50个字符作为标题
 * @param userMessage 用户消息
 * @returns 笔记标题
 */
export function generateNoteTitle(userMessage: Message): string {
  const content = userMessage.content.trim();
  if (content.length <= 50) {
    return content;
  }
  
  // 截取前50个字符，并在合适的位置截断（避免截断单词）
  let title = content.substring(0, 50);
  const lastSpace = title.lastIndexOf(' ');
  const lastPunctuation = Math.max(
    title.lastIndexOf('。'),
    title.lastIndexOf('？'),
    title.lastIndexOf('！'),
    title.lastIndexOf('，')
  );
  
  // 如果有标点符号且位置合理，在标点符号处截断
  if (lastPunctuation > 30) {
    title = title.substring(0, lastPunctuation + 1);
  } else if (lastSpace > 30) {
    // 否则在最后一个空格处截断
    title = title.substring(0, lastSpace);
  }
  
  return title.trim();
}

/**
 * 生成笔记内容
 * 使用AI回复的内容，排除thinking过程
 * @param aiMessage AI消息
 * @returns 笔记内容
 */
export function generateNoteContent(aiMessage: Message): string {
  // 只使用AI回复的主要内容，不包含thinking过程
  return aiMessage.content.trim();
}

/**
 * 确保"对话笔记"文件夹存在，如果不存在则创建
 * @returns Promise<文件夹ID或null>
 */
async function ensureChatNotesFolder(): Promise<string | null> {
  try {
    // 获取所有文件夹
    const folders = await noteAPI.listFolders();

    // 查找"对话笔记"文件夹
    const chatNotesFolder = folders.find(folder => folder.name === '对话笔记');

    if (chatNotesFolder) {
      return chatNotesFolder.id;
    }

    // 如果不存在，创建"对话笔记"文件夹
    const newFolder = await noteAPI.createFolder('对话笔记');
    return newFolder.id;
  } catch (error) {
    console.error('创建对话笔记文件夹失败:', error);
    return null;
  }
}

/**
 * 保存对话到笔记
 * @param userMessage 用户消息
 * @param aiMessage AI消息
 * @returns Promise<保存结果>
 */
export async function saveConversationToNote(
  userMessage: Message,
  aiMessage: Message
): Promise<{ success: boolean; noteId?: string; error?: string }> {
  try {
    const title = generateNoteTitle(userMessage);
    const content = generateNoteContent(aiMessage);

    // 确保"对话笔记"文件夹存在
    const folderId = await ensureChatNotesFolder();

    const response = await noteAPI.createNote({
      title,
      content,
      folder: folderId, // 使用文件夹ID而不是名称
      tags: ['对话', '自动生成']
    });

    return {
      success: true,
      noteId: response.id
    };
  } catch (error: any) {
    console.error('保存对话到笔记失败:', error);
    return {
      success: false,
      error: error.message || '保存失败'
    };
  }
}

/**
 * 从消息列表和消息ID保存对话到笔记
 * @param messages 消息列表
 * @param aiMessageId AI消息的ID
 * @returns Promise<保存结果>
 */
export async function saveConversationToNoteById(
  messages: Message[], 
  aiMessageId: string
): Promise<{ success: boolean; noteId?: string; error?: string }> {
  // 找到AI消息
  const aiMessageIndex = messages.findIndex(msg => msg.id === aiMessageId);
  if (aiMessageIndex === -1) {
    return {
      success: false,
      error: '未找到指定的消息'
    };
  }
  
  const aiMessage = messages[aiMessageIndex];
  if (aiMessage.role !== 'assistant') {
    return {
      success: false,
      error: '只能保存AI回复到笔记'
    };
  }
  
  // 找到对应的用户问题
  const userMessage = findUserQuestion(messages, aiMessageIndex);
  if (!userMessage) {
    return {
      success: false,
      error: '未找到对应的用户问题'
    };
  }
  
  return await saveConversationToNote(userMessage, aiMessage);
}
