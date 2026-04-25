from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Point(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)


class Region(BaseModel):
    left: int = Field(..., ge=0)
    top: int = Field(..., ge=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains_point(self, px: int, py: int) -> bool:
        return self.left <= px < self.right and self.top <= py < self.bottom

    def as_mss_dict(self, monitor_left: int = 0, monitor_top: int = 0) -> dict:
        return {
            "left": self.left + monitor_left,
            "top": self.top + monitor_top,
            "width": self.width,
            "height": self.height,
        }


class ScreenMap(BaseModel):
    monitor_index: int = Field(..., ge=1)
    screen_width: int = Field(..., gt=0)
    screen_height: int = Field(..., gt=0)
    browser_name: str = "chrome"

    tradovate_anchor_region: Region
    tradovate_anchor_reference_path: str

    # Anchor, price, and Cancel-All are required. Buy/Sell are optional so
    # the operator can calibrate incrementally — downstream code (click
    # executor) skips gracefully when those points are missing.
    price_region: Region

    buy_point: Point | None = None
    sell_point: Point | None = None
    cancel_all_point: Point

    position_region: Region | None = None
    status_region: Region | None = None
    pnl_region: Region | None = None
    instrument_label_region: Region | None = None
    # Signed-integer region showing current position size. Positive = long,
    # negative = short, 0 = flat. Source of truth for FLAT/LONG/SHORT
    # transitions — avoids halt-on-unknown-ack because the UI tells us the
    # real state. Side is derived from the sign.
    position_size_region: Region | None = None
    # Plain-decimal region showing the broker's verified entry (average fill)
    # price while in a position. Paired with position_size_region — together
    # they give the HUD everything it needs for live PnL without depending
    # on AckReader's fill-price OCR. Empty / blank when flat.
    entry_price_region: Region | None = None

    @field_validator("browser_name")
    @classmethod
    def _non_empty_browser(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("browser_name must not be empty")
        return v.strip().lower()

    def point_in_screen(self, point: Point) -> bool:
        return 0 <= point.x < self.screen_width and 0 <= point.y < self.screen_height

    def region_in_screen(self, region: Region) -> bool:
        return (
            region.left >= 0
            and region.top >= 0
            and region.right <= self.screen_width
            and region.bottom <= self.screen_height
        )
