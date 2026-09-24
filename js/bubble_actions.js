        function addBubbleToBranch(branchId, text, filePath, icon) {
            const branch = state.branches[branchId];
            if (!branch) return;
            if (!branch._bubbles) branch._bubbles = [];
            const bubble = {
                id: 'bub-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6),
                text: text,
                filePath: filePath,
                icon: icon || '📌'
            };
            branch._bubbles.push(bubble);
            saveBranches();
            updateHistoryList();
        }

        // 上传面板接受气泡拖拽
        document.addEventListener('DOMContentLoaded', () => {
            const dropZone = document.getElementById('upload-drop-zone');
            if (dropZone) {
                dropZone.addEventListener('dragover', (e) => {
                    // 接受从气泡拖拽过来的文件
                    if (e.dataTransfer.types.includes('application/file-name')) {
                        e.preventDefault();
                        e.dataTransfer.dropEffect = 'copy';
                        dropZone.classList.add('drag-over');
                    }
                });

                dropZone.addEventListener('dragleave', () => {
                    dropZone.classList.remove('drag-over');
                });

                dropZone.addEventListener('drop', (e) => {
                    e.preventDefault();
                    dropZone.classList.remove('drag-over');
                    const fileName = e.dataTransfer.getData('application/file-name');
                    const filePath = e.dataTransfer.getData('application/file-path');
                    if (fileName) {
                        // 模拟添加文件到上传列表
                        // 由于是浏览器环境无法直接读取本地文件路径，
                        // 提示用户手动选择该文件上传
                        const fileInput = document.getElementById('file-attachment-input');
                        const dataTransfer = new DataTransfer();
                        // 无法直接通过路径创建 File 对象，提示用户
                        if (filePath) {
                            showPassiveToast(`已识别文件: ${fileName}\n请手动选择该文件上传（浏览器安全限制，无法直接读取磁盘文件）`);
                        } else {
                            showPassiveToast(`已识别文件: ${fileName}\n请手动选择该文件上传`);
                        }
                        // 触发上传面板展开
                        if (typeof showUploadPanel === 'function') {
                            showUploadPanel();
                        }
                    }
                });
            }
        });

        // 暴露到全局
        window.removePendingAttachment = removePendingAttachment;

          async function extractTextFromFile(file) {
              const extension = file.name.split('.').pop().toLowerCase();
              const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.svg'];

              // 1. PDF 由【后端】处理（/api/file/upload 用 fitz 提取），不在前端用 pdfjs 解析
              if (extension === 'pdf') {
                  return '[PDF 文件：文本由后端 file_id 注入，前端不解析]';
              }

              // 2. Word (.docx) 文件解析
              if (extension === 'docx') {
                  try {
                      const arrayBuffer = await file.arrayBuffer();
                      const result = await mammoth.extractRawText({ arrayBuffer: arrayBuffer });
                      return result.value;
                  } catch (e) {
                      throw new Error('Word 文档解析失败：' + e.message);
                  }
              }

              // 3. PPT (.pptx) 文件解析（ZIP中的XML提取文本）
              if (extension === 'pptx') {
                  try {
                      const arrayBuffer = await file.arrayBuffer();
                      const zip = await JSZip.loadAsync(arrayBuffer);
                      let text = '';
                      const slideFiles = Object.keys(zip.files).filter(f => f.startsWith('ppt/slides/slide') && f.endsWith('.xml')).sort();
                      const ns = 'http://schemas.openxmlformats.org/drawingml/2006/main';
                      for (const slideFile of slideFiles) {
                          const content = await zip.file(slideFile).async('string');
                          const parser = new DOMParser();
                          const xml = parser.parseFromString(content, 'text/xml');
                          const texts = xml.getElementsByTagNameNS(ns, 't');
                          text += Array.from(texts).map(el => el.textContent).join(' ') + '\n';
                      }
                      return text.trim() || '[PPT 未能提取到文本内容]';
                  } catch (e) {
                      throw new Error('PPT 解析失败：' + e.message);
                  }
              }

              // 4. ODT 文件解析（ZIP中的content.xml提取文本）
              if (extension === 'odt') {
                  try {
                      const arrayBuffer = await file.arrayBuffer();
                      const zip = await JSZip.loadAsync(arrayBuffer);
                      const contentFile = zip.file('content.xml');
                      if (!contentFile) return '[ODT 文件格式异常]';
                      const content = await contentFile.async('string');
                      const parser = new DOMParser();
                      const xml = parser.parseFromString(content, 'text/xml');
                      const ns = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0';
                      const paragraphs = xml.getElementsByTagNameNS(ns, 'p');
                      let result = '';
                      for (const el of paragraphs) {
                          result += el.textContent + '\n';
                      }
                      return result.trim() || '[ODT 未能提取到文本内容]';
                  } catch (e) {
                      throw new Error('ODT 解析失败：' + e.message);
                  }
              }

              // 5. SVG 文件 - 读取为 XML 源码文本
              if (extension === 'svg') {
                  const text = await file.text();
                  return text;
              }

              // 6. 图片文件 —— 前端不解析、不 OCR、不转 base64。
              //    图片随消息只带 file_id，智能体按需调 read_image(file_id, mode) 自行决定
              //    base64 还是 OCR（用户 2026-09-22 定：上传一律走 read_file 系列工具）。
              if (imageExtensions.includes('.' + extension)) {
                  return '[图片文件：正文由智能体按 file_id 调用 read_image 获取]';
              }

              // 7. Excel 文件 - 尝试读取为文本
              if (extension === 'xlsx' || extension === 'xls') {
                  // .xlsx 是 ZIP 压缩格式，无法直接读取
                  // 提示用户转换为 CSV 或文本格式
                  return '[Excel 文件无法直接读取，请将文件转换为 .csv 或 .txt 格式后再上传]';
              }

              // 8. 文本类文件解析 (默认)
              return new Promise((resolve, reject) => {
                  const reader = new FileReader();
                  reader.onload = (e) => resolve(e.target.result);
                  reader.onerror = (e) => reject(new Error('文件读取失败'));
                  reader.readAsText(file);
              });
          }


        // ==================== 初始化应用 ====================
