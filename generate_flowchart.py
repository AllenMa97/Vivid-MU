"""
Generate VividMU Pipeline Flowchart as PNG/PDF
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

COLORS = {
    'input': '#ff6b6b',
    'preprocess': '#4ecdc4',
    'coarse': '#45b7d1',
    'fine': '#96ceb4',
    'segment': '#f9ca24',
    'vlvm': '#a29bfe',
    'audio': '#fd79a8',
    'llm': '#00b894',
    'video_gen': '#e17055',
    'output': '#ffeaa7',
    'bg': '#1a1a3e',
    'text': '#ffffff',
    'text_dark': '#333333',
}

def draw_box(ax, x, y, width, height, text, color, fontsize=9, text_color=None):
    if text_color is None:
        text_color = COLORS['text'] if color not in [COLORS['segment'], COLORS['output']] else COLORS['text_dark']
    
    box = FancyBboxPatch((x - width/2, y - height/2), width, height,
                         boxstyle="round,pad=0.02,rounding_size=0.1",
                         facecolor=color, edgecolor='white', linewidth=1.5)
    ax.add_patch(box)
    ax.text(x, y, text, ha='center', va='center', fontsize=fontsize,
            color=text_color, fontweight='bold', wrap=True)

def draw_subgraph_box(ax, x, y, width, height, title, color='#ffffff20'):
    box = FancyBboxPatch((x - width/2, y - height/2), width, height,
                         boxstyle="round,pad=0.02,rounding_size=0.15",
                         facecolor=color, edgecolor='#ffffff40', linewidth=1, linestyle='--')
    ax.add_patch(box)

def draw_arrow(ax, start, end, color='#ffffff80'):
    ax.annotate('', xy=end, xytext=start,
                arrowprops=dict(arrowstyle='->', color=color, lw=1.5))

def main():
    fig, ax = plt.subplots(1, 1, figsize=(20, 28))
    ax.set_xlim(0, 20)
    ax.set_ylim(0, 28)
    ax.set_aspect('equal')
    ax.axis('off')
    fig.patch.set_facecolor(COLORS['bg'])
    ax.set_facecolor(COLORS['bg'])
    
    ax.text(10, 27.3, 'VividMU', fontsize=24, ha='center', va='center',
            color='#00d4ff', fontweight='bold')
    ax.text(10, 26.7, '视频精彩片段提取系统 - 端到端流程', fontsize=12, ha='center', va='center', color='#888888')
    
    y = 25.5
    draw_box(ax, 10, y, 3, 0.6, '原始视频文件', COLORS['input'], fontsize=10)
    
    y -= 1.2
    ax.text(10, y + 0.8, 'Step 1: 传统CV/音频特征过滤', fontsize=12, ha='center', va='center', color='#00d4ff', fontweight='bold')
    
    y -= 0.5
    draw_box(ax, 10, y, 3.5, 0.5, '视频压缩\n降低分辨率以加速处理', COLORS['preprocess'], fontsize=8)
    draw_arrow(ax, (10, y + 1.1), (10, y + 0.3))
    
    y -= 1.2
    ax.text(10, y + 0.6, '粗筛选层', fontsize=10, ha='center', va='center', color='#45b7d1')
    
    y -= 0.6
    x_positions = [4, 8, 12, 16]
    labels = ['dHash感知哈希\n去除重复帧', '光流分析\n检测运动强度', '音频能量检测\n识别静音片段', '综合评分过滤']
    for i, (x_pos, label) in enumerate(zip(x_positions, labels)):
        if i < 3:
            draw_box(ax, x_pos, y, 3, 0.8, label, COLORS['coarse'], fontsize=7)
        else:
            draw_box(ax, x_pos, y, 2.5, 0.8, label, COLORS['coarse'], fontsize=7)
    
    for x_pos in x_positions[:3]:
        draw_arrow(ax, (x_pos, y + 1.3), (x_pos, y + 0.5))
    
    draw_arrow(ax, (4, y - 0.5), (16, y - 0.5))
    for x_pos in x_positions[:3]:
        ax.plot([x_pos, x_pos], [y - 0.5, y - 0.4], color='#ffffff80', lw=1.5)
    
    y -= 1.5
    ax.text(10, y + 0.6, '细筛选层', fontsize=10, ha='center', va='center', color='#96ceb4')
    
    y -= 0.6
    x_positions = [4, 8, 12, 16]
    labels = ['人脸检测\nMTCNN', '场景分类\nResNet50', '稳定性评分\n帧间差异', '综合评分排序']
    for i, (x_pos, label) in enumerate(zip(x_positions, labels)):
        draw_box(ax, x_pos, y, 3, 0.8, label, COLORS['fine'], fontsize=7)
    
    draw_arrow(ax, (16, y + 1.5), (16, y + 0.5))
    for x_pos in x_positions[:3]:
        draw_arrow(ax, (x_pos, y + 1.5), (x_pos, y + 0.5))
    
    y -= 1.3
    draw_box(ax, 10, y, 3.5, 0.6, '视频切割\n按原分辨率输出', COLORS['segment'], fontsize=8, text_color=COLORS['text_dark'])
    draw_arrow(ax, (16, y + 0.9), (10, y + 0.4))
    
    y -= 1.2
    ax.text(10, y + 0.8, 'Step 2: AI语义分析筛选', fontsize=12, ha='center', va='center', color='#00d4ff', fontweight='bold')
    
    y -= 0.5
    ax.text(10, y + 0.4, '特征提取', fontsize=10, ha='center', va='center', color='#aaaaaa')
    
    y -= 0.5
    draw_box(ax, 6, y, 2.8, 0.5, '关键帧提取', COLORS['preprocess'], fontsize=8)
    draw_box(ax, 14, y, 2.8, 0.5, '音频提取', COLORS['preprocess'], fontsize=8)
    draw_arrow(ax, (10, y + 1), (6, y + 0.3))
    draw_arrow(ax, (10, y + 1), (14, y + 0.3))
    
    y -= 1
    ax.text(10, y + 0.4, '多模态AI分析 (含自动回退机制)', fontsize=10, ha='center', va='center', color='#aaaaaa')
    
    y -= 0.6
    draw_box(ax, 4, y, 3, 0.8, 'LVLM\n视觉理解', COLORS['vlvm'], fontsize=8)
    draw_box(ax, 10, y, 3, 0.8, 'Audio LLM\n音频理解', COLORS['audio'], fontsize=8)
    draw_box(ax, 16, y, 3, 0.8, 'LLM\n综合筛选', COLORS['llm'], fontsize=8)
    
    draw_arrow(ax, (6, y + 0.5), (4, y + 0.5))
    draw_arrow(ax, (14, y + 0.5), (10, y + 0.5))
    draw_arrow(ax, (4, y - 0.5), (16, y - 0.3))
    draw_arrow(ax, (10, y - 0.5), (16, y - 0.3))
    
    y -= 1.2
    ax.text(10, y + 0.4, '片段选择', fontsize=10, ha='center', va='center', color='#aaaaaa')
    
    y -= 0.5
    draw_box(ax, 10, y, 3.5, 0.5, '多方案生成\n精彩/叙事/氛围', COLORS['llm'], fontsize=8)
    draw_arrow(ax, (16, y + 1.1), (10, y + 0.3))
    
    y -= 1.2
    ax.text(10, y + 0.8, 'Step 3: 视频生成', fontsize=12, ha='center', va='center', color='#00d4ff', fontweight='bold')
    
    y -= 0.5
    draw_box(ax, 10, y, 4, 0.6, '视频生成模型\n提示词模板注入素材片段', COLORS['video_gen'], fontsize=8)
    draw_arrow(ax, (10, y + 1.1), (10, y + 0.4))
    
    y -= 1
    draw_box(ax, 10, y, 3, 0.6, '最终视频输出', COLORS['output'], fontsize=10, text_color=COLORS['text_dark'])
    draw_arrow(ax, (10, y + 0.8), (10, y + 0.4))
    
    y -= 1.5
    ax.text(10, y + 0.3, 'Step 3 通过设计不同的提示词模板，向 Kling 等视频剪辑AI供应商注入素材片段，生成不同风格的最终视频',
            fontsize=9, ha='center', va='center', color='#f9ca24', style='italic')
    
    y -= 1.2
    legend_items = [
        ('输入/输出', COLORS['input']),
        ('预处理', COLORS['preprocess']),
        ('粗筛选', COLORS['coarse']),
        ('细筛选', COLORS['fine']),
        ('LVLM', COLORS['vlvm']),
        ('Audio LLM', COLORS['audio']),
        ('LLM', COLORS['llm']),
        ('视频生成', COLORS['video_gen']),
    ]
    
    legend_x = 2
    for label, color in legend_items:
        rect = mpatches.Rectangle((legend_x, y), 0.4, 0.3, facecolor=color, edgecolor='white', linewidth=1)
        ax.add_patch(rect)
        ax.text(legend_x + 0.6, y + 0.15, label, fontsize=8, va='center', color='white')
        legend_x += 2.3
    
    y -= 1.5
    ax.text(10, y + 0.3, '支持的模型列表', fontsize=12, ha='center', va='center', color='#00d4ff', fontweight='bold')
    
    y -= 0.8
    models = [
        ('LVLM (视觉语言模型)', '支持: Qwen-VL-Flash, Qwen-VL-Plus, Qwen-VL-Max, GLM-4V', COLORS['vlvm']),
        ('Audio LLM (音频语言模型)', '支持: Qwen-Audio-Turbo, Qwen-Audio-Latest, Qwen2-Audio', COLORS['audio']),
        ('LLM (大语言模型)', '支持: Qwen-Max, Qwen-Flash, Qwen3.5-Flash, GLM-4, DeepSeek', COLORS['llm']),
        ('视频生成模型', '支持: Kling AI, Runway Gen-3, Sora', COLORS['video_gen']),
    ]
    
    model_x = [3, 8, 13, 18]
    for i, (title, desc, color) in enumerate(models):
        rect = FancyBboxPatch((model_x[i] - 2.3, y - 0.5), 4.6, 1,
                              boxstyle="round,pad=0.02,rounding_size=0.1",
                              facecolor='#ffffff10', edgecolor=color, linewidth=2)
        ax.add_patch(rect)
        ax.text(model_x[i], y + 0.15, title, fontsize=8, ha='center', va='center', color=color, fontweight='bold')
        ax.text(model_x[i], y - 0.2, desc, fontsize=6, ha='center', va='center', color='#aaaaaa')
    
    plt.tight_layout()
    
    output_dir = 'd:/smart_cliping/docs'
    plt.savefig(f'{output_dir}/pipeline_flowchart.png', dpi=150, bbox_inches='tight', 
                facecolor=COLORS['bg'], edgecolor='none')
    plt.savefig(f'{output_dir}/pipeline_flowchart.pdf', bbox_inches='tight',
                facecolor=COLORS['bg'], edgecolor='none')
    
    print(f"PNG saved to: {output_dir}/pipeline_flowchart.png")
    print(f"PDF saved to: {output_dir}/pipeline_flowchart.pdf")

if __name__ == '__main__':
    main()
