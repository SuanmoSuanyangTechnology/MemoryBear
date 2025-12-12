import subprocess
import os
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException, status

# 根据CPU核心数自动设置（保守策略：核心数 * 2）
MAX_WORKERS = os.cpu_count() * 2 if os.cpu_count() else 4
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# 将DOCX/PPT/PPTX文件转换为PDF
def convert_to_pdf(src_path):
    try:
        print("开始使用LibreOffice将DOC/DOCX/PPT/PPTX转换为PDF...")
        output_dir = os.path.dirname(src_path)

        # 使用linux上LibreOffice的完整路径调用soffice进行转换
        libreoffice_path = "/usr/bin/soffice"
        if not os.path.exists(libreoffice_path):
            # 使用macOS上LibreOffice的完整路径调用soffice进行转换
            libreoffice_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        if not os.path.exists(libreoffice_path):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="LibreOffice未安装或路径不正确，请确认安装。"
            )

        # 使用subprocess.run的超时设置防止卡死
        subprocess.run([
            libreoffice_path,
            '--headless',
            '--convert-to', 'pdf',
            '--outdir', output_dir,
            src_path
        ], check=True, timeout=120)  # 设置超时时间

        # 检查PDF是否生成成功
        dest_path = os.path.splitext(src_path)[0] + '.pdf'
        if not os.path.exists(dest_path):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"PDF文件未生成在 {dest_path}"
            )

        print(f"PDF已保存至 {dest_path}")
        return dest_path
    except subprocess.CalledProcessError as e:
        print(f"转换过程中出错: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"转换过程中出错: {e}"
        )
    except FileNotFoundError as e:
        print(f"文件错误: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件错误: {e}"
        )

def async_convert_to_pdf(src_path):
    # 提交任务到线程池
    future = executor.submit(convert_to_pdf, src_path)
    return future  # 返回一个future对象，调用者可以使用它来获取结果或处理异常