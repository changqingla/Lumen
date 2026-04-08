import pdfIconUrl from '@/assets/pdf.svg';
import docIconUrl from '@/assets/doc.svg';
import docxIconUrl from '@/assets/docx.svg';
import txtIconUrl from '@/assets/TXT.svg';
import mdIconUrl from '@/assets/file-markdown.svg';

/**
 * 根据文件名获取对应的图标URL
 */
export function getFileIcon(filename: string): string {
  const ext = filename.toLowerCase().split('.').pop();
  switch (ext) {
    case 'pdf':
      return pdfIconUrl;
    case 'doc':
      return docIconUrl;
    case 'docx':
      return docxIconUrl;
    case 'txt':
      return txtIconUrl;
    case 'md':
    case 'markdown':
      return mdIconUrl;
    default:
      return pdfIconUrl; // 默认使用 PDF 图标
  }
}

export { pdfIconUrl, docIconUrl, docxIconUrl, txtIconUrl, mdIconUrl };
