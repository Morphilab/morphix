# features/analytics/services/analytics_service.py
import logging
import os
import re
import time

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.database import get_async_session
from core.models import Conversation

logger = logging.getLogger(__name__)

CHARTS_DIR = "analytics_charts"
os.makedirs(CHARTS_DIR, exist_ok=True)


class AnalyticsService:
    """Servicio completo de Analytics - versión asíncrona."""

    @staticmethod
    async def load_analytics_data():  # <-- Ahora es async
        """Carga y procesa todas las conversaciones de forma asíncrona."""
        async with get_async_session() as session:
            stmt = select(Conversation).options(selectinload(Conversation.messages))
            result = await session.execute(stmt)
            convs = result.scalars().all()

            if not convs:
                return None, None

            data = []
            for c in convs:
                msgs = c.messages

                content_str = " ".join([m.content for m in msgs])
                tags_str = c.tags or ""
                parse_str = tags_str + " " + content_str

                # Extraer tokens
                tokens_match = re.search(
                    r"[\•*]\s*\*\*Tokens reales:\*\*\s*(\d+)", parse_str, re.IGNORECASE
                )
                tokens = int(tokens_match.group(1)) if tokens_match else 0

                # Extraer calidad
                quality_llm_match = re.search(r"\(LLM:\s*(\d+)/10\)", parse_str, re.IGNORECASE)
                if quality_llm_match:
                    quality = int(quality_llm_match.group(1))
                else:
                    quality_str_match = re.search(
                        r"[\•*]\s*\*\*Calidad:\*\*\s*(Alta|Media)", parse_str, re.IGNORECASE
                    )
                    quality = (
                        8
                        if quality_str_match and "alta" in quality_str_match.group(0).lower()
                        else 5
                    )

                data.append(
                    {
                        "id": c.id,
                        "title": c.title,
                        "created_at": c.created_at,
                        "tags": tags_str,
                        "tokens": tokens,
                        "quality": quality,
                    }
                )

            import pandas as pd

            df = pd.DataFrame(data)
            logger.info(f"Analytics cargados: {len(df)} conversaciones")
            return df, convs

    @staticmethod
    def generate_charts(df):
        """Genera los gráficos y devuelve las rutas (sin cambios)."""
        import matplotlib.pyplot as plt

        if df is None or df.empty:
            return None, None

        timestamp = int(time.time())

        # Token chart
        tokens_path = None
        if df["tokens"].sum() > 0:
            plt.figure(figsize=(12, 6))
            plt.plot(
                df["created_at"],
                df["tokens"],
                marker="o",
                linestyle="-",
                linewidth=3,
                markersize=10,
                color="royalblue",
            )
            plt.title("Uso de Tokens por Conversación", fontsize=18, fontweight="bold")
            plt.xlabel("Fecha")
            plt.ylabel("Tokens")
            plt.grid(True, alpha=0.4)
            plt.tight_layout()
            tokens_path = f"{CHARTS_DIR}/tokens_{timestamp}.png"
            plt.savefig(tokens_path, dpi=200)
            plt.close()

        # Quality chart
        quality_path = None
        avg_quality = df.groupby("tags")["quality"].mean()
        if avg_quality.sum() > 0:
            plt.figure(figsize=(12, 6))
            bars = avg_quality.plot(kind="bar", color="lightcoral", edgecolor="black", width=0.6)
            plt.title("Calidad Promedio por Tipo de Conversación", fontsize=18, fontweight="bold")
            plt.ylabel("Puntuación")
            plt.xticks(rotation=45, ha="right")
            plt.grid(True, alpha=0.4, axis="y")
            plt.tight_layout()

            for bar in bars.patches:
                height = bar.get_height()
                plt.text(
                    bar.get_x() + bar.get_width() / 2,
                    height + 0.2,
                    f"{height:.1f}",
                    ha="center",
                    fontsize=12,
                    fontweight="bold",
                )

            quality_path = f"{CHARTS_DIR}/quality_{timestamp}.png"
            plt.savefig(quality_path, dpi=200)
            plt.close()

        return tokens_path, quality_path
