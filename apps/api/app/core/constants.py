"""跨模块共享常量(无依赖叶子模块,任意层可 import)。"""

# 管理端「固定截断」列表的统一上限(发票/注销申请/公告历史等),
# 与 admin/components/ListCapNote.tsx 的 LIST_CAPS 对齐
ADMIN_LIST_CAP = 200
