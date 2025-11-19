# app/services/shap_service.py

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Optional

import numpy as np
import pandas as pd
import shap
from loguru import logger


@dataclass
class ShapFeatureImpact:
    """1つの特徴量に対するSHAP影響度情報"""

    name: str
    mean_abs_shap: float
    rank: int


def _normalize_background_frame(
    X: pd.DataFrame,
    max_background: int = 2000,
    feature_names: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """
    背景サンプル用に DataFrame を整理するヘルパ。
    - 列順を feature_names にそろえる（指定があれば）
    - 行数が多すぎる場合は max_background までサンプリング
    """
    if X is None or X.empty:
        raise ValueError("SHAP計算用の背景データが空です。")

    df = X.copy()

    if feature_names is not None:
        missing = set(feature_names) - set(df.columns)
        if missing:
            raise ValueError(
                f"SHAP背景データに不足している特徴量があります: {sorted(missing)}"
            )
        # 列順を揃える
        df = df.loc[:, list(feature_names)]

    if len(df) > max_background:
        logger.info(
            "SHAP背景サンプルを {orig} 行 → {sub} 行にサンプリングします。",
            orig=len(df),
            sub=max_background,
        )
        df = df.sample(n=max_background, random_state=42)

    return df


def compute_shap_feature_importance(
    model,
    X: pd.DataFrame,
    *,
    feature_names: Optional[Sequence[str]] = None,
    top_n: int = 20,
    max_background: int = 2000,
) -> List[ShapFeatureImpact]:
    """
    LightGBM などツリーモデルに対して SHAP (TreeExplainer) を使って
    グローバルな特徴量重要度（平均絶対SHAP値）を計算する。

    Parameters
    ----------
    model : 学習済みモデル（LightGBMClassifier想定だが、ツリーモデルなら概ねOK）
    X : 背景データの特徴量 DataFrame
    feature_names : 列順を明示したい場合の特徴量名リスト
    top_n : 上位何個まで返すか
    max_background : 背景サンプルの最大行数（重くなるのを防ぐフィルタ）

    Returns
    -------
    List[ShapFeatureImpact]
    """
    df_bg = _normalize_background_frame(
        X, max_background=max_background, feature_names=feature_names
    )

    logger.info(
        "SHAP計算開始: rows={rows}, cols={cols}, top_n={top_n}",
        rows=len(df_bg),
        cols=df_bg.shape[1],
        top_n=top_n,
    )

    # LightGBM/sklearn 互換のツリーモデルなら TreeExplainer が速い
    explainer = shap.TreeExplainer(model)

    shap_values = explainer.shap_values(df_bg)

    # shap_values の形はモデルによって違うので頑張って正規化する
    if isinstance(shap_values, list):
        # クラスごとの配列リスト（n_class, n_sample, n_feature）想定
        arr = np.stack(shap_values, axis=0)  # (n_class, n_sample, n_feature)
        mean_abs = np.mean(np.abs(arr), axis=(0, 1))  # feature 次元に集約
    else:
        arr = np.asarray(shap_values)  # (n_sample, n_feature)
        mean_abs = np.mean(np.abs(arr), axis=0)

    # 念のため shape を確認
    if mean_abs.shape[0] != df_bg.shape[1]:
        raise RuntimeError(
            f"SHAP重要度の次元数 ({mean_abs.shape[0]}) と特徴量数 "
            f"({df_bg.shape[1]}) が一致しません。"
        )

    features = list(df_bg.columns)
    # 大きい順にソート
    order = np.argsort(-mean_abs)
    items: List[ShapFeatureImpact] = []

    for idx, feat_idx in enumerate(order):
        if top_n is not None and idx >= top_n:
            break
        items.append(
            ShapFeatureImpact(
                name=features[int(feat_idx)],
                mean_abs_shap=float(mean_abs[int(feat_idx)]),
                rank=idx + 1,
            )
        )

    logger.info("SHAP計算完了: 返却件数={cnt}", cnt=len(items))
    return items


def shap_items_to_frame(items: List[ShapFeatureImpact]) -> pd.DataFrame:
    """
    ShapFeatureImpact のリストを pandas.DataFrame に変換するヘルパ。
    GUI や CLI で扱いやすくするため。
    """
    if not items:
        return pd.DataFrame(columns=["rank", "feature", "mean_abs_shap"])

    data = {
        "rank": [it.rank for it in items],
        "feature": [it.name for it in items],
        "mean_abs_shap": [it.mean_abs_shap for it in items],
    }
    return pd.DataFrame(data).sort_values("rank").reset_index(drop=True)
