"""①②データ収集レイヤ。

`DataSource` インターフェースを実装すれば、取得元（netkeiba / CSV / JRA-VAN など）を
自由に差し替えられる。今フェーズの主役は netkeiba スクレイピング。
"""

from .base import DataSource

__all__ = ["DataSource"]
