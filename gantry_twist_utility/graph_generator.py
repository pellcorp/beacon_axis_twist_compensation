# Gantry Twist Analysis
#
# Graph generation for Beacon Offset Analysis
#
# Copyright (C) 2025 omgitsgio <gio@omgitsgio.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

"""
Beacon Offset Analysis Tool
Parses console output from BEACON_OFFSET_COMPARE commands and analyzes consistency.
"""

import numpy as np
import matplotlib
# Ensure non-GUI backend to reduce memory footself.self.gcmd.respond_info and leaks in headless envs
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from scipy.interpolate import griddata
from matplotlib.tri import Triangulation
from mpl_toolkits.mplot3d.art3d import Line3DCollection
from pathlib import Path
from datetime import datetime
import gc

class GraphGenerator:
    """Graph generator for standalone debugging."""

    def __init__(self, gcmd, output_folder, collected_data, debug: bool = False, meta: dict = None):
        self.gcmd = gcmd
        self.output_folder = Path(output_folder).expanduser()
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.offset_data = collected_data
        self.debug = bool(debug)
        self.meta = meta or {}
        self.sampling_direction = str(self.meta.get('sampling_direction', 'xy'))

    def _compose_subtitle(self):
        """Build graph subtitles from meta info."""
        
        parts = []

        # Temperatures
        temps = []
        if 'hotend_temp' in self.meta and self.meta['hotend_temp'] is not None:
            temps.append(f"Hotend {float(self.meta['hotend_temp']):.0f}°C")
        if 'bed_temp' in self.meta and self.meta['bed_temp'] is not None:
            temps.append(f"Bed {float(self.meta['bed_temp']):.0f}°C")
        if temps:
            parts.append(f"Temps: {', '.join(temps)}")
        
        # Grid size
        if 'grid_size' in self.meta:
            gs = int(float(self.meta['grid_size']))
            parts.append(f"Grid: {gs}×{gs}")
        
        # Bed dimensions from bounds
        if 'bounds' in self.meta and isinstance(self.meta['bounds'], dict):
            bounds = self.meta['bounds']
            if 'min_x' in bounds and 'max_x' in bounds and 'min_y' in bounds and 'max_y' in bounds:
                parts.append(f"Area: min {float(bounds['min_x']):.0f},{float(bounds['min_y']):.0f}mm"
                             f"; max {float(bounds['max_x']):.0f},{float(bounds['max_y']):.0f} mm")

        # Successful points
        if 'points_completed' in self.meta and 'total_points' in self.meta:
            completed = int(self.meta['points_completed'])
            total = int(self.meta['total_points'])
            parts.append(f"Points: {completed}/{total} successful")
        
        return " | ".join(parts) if parts else ""

    def plot_analysis(self):
        """Create visualization of offset data with enhanced pattern analysis."""
        if len(self.offset_data) < 2:
            self.gcmd.respond_info("ERROR: Not enough data points to generate graphs (need at least 2)")
            return None

        # Extract NumPy arrays from offset_data structure: {'x','y','delta', ...}
        positions = []
        offsets_list = []
        contact_zs = []
        proximity_zs = []
        
        for pt in self.offset_data:
            try:
                x = float(pt.get('x')) 
                y = float(pt.get('y')) 
                positions.append([x, y])
                d = pt.get('delta')
                offsets_list.append(float(d) if d is not None else None)
                contact_z = pt.get('contact_z')
                proximity_z = pt.get('proximity_z')
                contact_zs.append(float(contact_z) if contact_z is not None else None)
                proximity_zs.append(float(proximity_z) if proximity_z is not None else None)

            except (ValueError, TypeError) as e:
                self.gcmd.respond_info(f"Warning: Error at point {pt}: {e}")

                if positions and offsets_list:  # Check if there are points to remove in case of error
                    positions.pop()
                    offsets_list.pop()
                    contact_zs.pop()
                    proximity_zs.pop()

        positions = np.array(positions, dtype=float)
        offsets = np.array(offsets_list, dtype=float)
        contact_zs = np.array(contact_zs, dtype=float)
        proximity_zs = np.array(proximity_zs, dtype=float)

        # Prepare bounds and stats
        x_min, x_max = positions[:, 0].min(), positions[:, 0].max()
        y_min, y_max = positions[:, 1].min(), positions[:, 1].max()
        x_margin = (x_max - x_min) * 0.05
        y_margin = (y_max - y_min) * 0.05

        # Timestamp and debug labelling
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        title_dbg = " (DEBUG)" if self.debug else ""
        file_tag_dbg = "_DEBUG" if self.debug else ""

        # -------- Page 1: Six 2D plots --------
        fig1, axes = plt.subplots(2, 3, figsize=(18, 10))
        fig1.suptitle(f'Beacon Delta Offset Pattern Analysis{title_dbg}', fontsize=16, fontweight='bold', y=0.98)
        try:
            subtitle = self._compose_subtitle()
            if subtitle:
                fig1.text(0.5, 0.955, subtitle, ha='center', va='top', fontsize=10, color='dimgray')
        except Exception:
            pass

        # 1. 2D scatter heatmap
        scatter = axes[0,0].scatter(positions[:, 0], positions[:, 1],
                                    c=offsets*1000, cmap='RdBu_r', s=100, alpha=0.8,
                                    edgecolors='black', linewidth=0.5)
        axes[0,0].set_xlim(x_min - x_margin, x_max + x_margin)
        axes[0,0].set_ylim(y_min - y_margin, y_max + y_margin)
        axes[0,0].set_xlabel('X Position (mm)')
        axes[0,0].set_ylabel('Y Position (mm)')
        axes[0,0].set_title('Delta Pattern Across Bed')
        axes[0,0].grid(True, alpha=0.3)
        axes[0,0].set_aspect('equal', adjustable='box')
        plt.colorbar(scatter, ax=axes[0,0], label='Delta (µm)')

        # Contours
        if len(positions) > 10:
            xi = np.linspace(x_min - x_margin, x_max + x_margin, 50)
            yi = np.linspace(y_min - y_margin, y_max + y_margin, 50)
            xi, yi = np.meshgrid(xi, yi)
            zi = griddata(positions, offsets*1000, (xi, yi), method='cubic', fill_value=np.nan)
            contours = axes[0,0].contour(xi, yi, zi, levels=8, colors='white', alpha=0.6, linewidths=1)
            axes[0,0].clabel(contours, inline=True, fontsize=8, fmt='%.1f')

        # 2. X-axis trend
        sorted_idx = np.argsort(positions[:, 0])
        sorted_x = positions[sorted_idx, 0]
        sorted_offsets_x = offsets[sorted_idx]
        axes[0,1].scatter(positions[:, 0], offsets*1000, alpha=0.6, s=50)
        axes[0,1].plot(sorted_x, sorted_offsets_x*1000, 'r-', alpha=0.8, linewidth=2)
        x_corr = np.corrcoef(positions[:, 0], offsets)[0, 1] if len(positions) > 2 else 0
        if abs(x_corr) > 0.3:
            z = np.polyfit(positions[:, 0], offsets*1000, 1)
            p = np.poly1d(z)
            axes[0,1].plot(positions[:, 0], p(positions[:, 0]), 'g--', alpha=0.8, linewidth=2,
                           label=f'Trend (r={x_corr:.3f})')
            axes[0,1].legend()
        # Highlight per-column delta spread on X-axis when range exceeds 50 µm
        highlight_threshold_um = 50.0
        x_group_keys = np.round(positions[:, 0], 3)
        unique_x_vals = np.unique(x_group_keys)
        if unique_x_vals.size:
            sorted_x_vals = np.sort(unique_x_vals)
            if unique_x_vals.size > 1:
                typical_gap_x = np.median(np.diff(sorted_x_vals))
            else:
                typical_gap_x = max((x_max - x_min) * 0.05, 1.0)
            x_out_of_range_count = 0
            overall_span_um = max((np.nanmax(offsets) - np.nanmin(offsets)) * 1000, 1.0)
            label_pad_um = max(5.0, overall_span_um * 0.03)
            for idx, x_val in enumerate(sorted_x_vals):
                mask = x_group_keys == x_val
                group_offsets = offsets[mask]
                if group_offsets.size < 2:
                    continue
                group_range_um = (np.nanmax(group_offsets) - np.nanmin(group_offsets)) * 1000
                if group_range_um <= highlight_threshold_um:
                    continue
                x_out_of_range_count += 1
                prev_gap = x_val - sorted_x_vals[idx-1] if idx > 0 else typical_gap_x
                next_gap = sorted_x_vals[idx+1] - x_val if idx < unique_x_vals.size - 1 else typical_gap_x
                left = x_val - prev_gap / 2.0
                right = x_val + next_gap / 2.0
                left = max(left, x_min - x_margin)
                right = min(right, x_max + x_margin)
                group_max_um = np.nanmax(group_offsets) * 1000
                axes[0,1].axvspan(left, right, color='orange', alpha=0.15, zorder=0)
                axes[0,1].text(x_val, group_max_um + label_pad_um, f'{group_range_um:.0f}µm',
                               ha='center', va='bottom', fontsize=9, color='darkorange',
                               bbox=dict(facecolor='white', edgecolor='darkorange', boxstyle='round,pad=0.2', alpha=0.85),
                               clip_on=False)
            note_color_x = 'darkorange' if x_out_of_range_count else 'dimgray'
            axes[0,1].annotate(
                f'{x_out_of_range_count}/{unique_x_vals.size} positions > {highlight_threshold_um:.0f} µm',
                xy=(0.0, 1.0), xycoords='axes fraction',
                xytext=(0, 12), textcoords='offset points',
                ha='left', va='bottom', fontsize=9, color=note_color_x,
                bbox=dict(facecolor='white', edgecolor=note_color_x, boxstyle='round,pad=0.2', alpha=0.85))
        axes[0,1].set_xlabel('X Position (mm)')
        axes[0,1].set_ylabel('Delta (µm)')
        axes[0,1].set_title('X-Axis Trend Analysis')
        axes[0,1].grid(True, alpha=0.3)

        # 3. Y-axis trend
        sorted_idx = np.argsort(positions[:, 1])
        sorted_y = positions[sorted_idx, 1]
        sorted_offsets_y = offsets[sorted_idx]
        axes[0,2].scatter(positions[:, 1], offsets*1000, alpha=0.6, s=50)
        axes[0,2].plot(sorted_y, sorted_offsets_y*1000, 'r-', alpha=0.8, linewidth=2)
        y_corr = np.corrcoef(positions[:, 1], offsets)[0, 1] if len(positions) > 2 else 0
        if abs(y_corr) > 0.3:
            z = np.polyfit(positions[:, 1], offsets*1000, 1)
            p = np.poly1d(z)
            axes[0,2].plot(positions[:, 1], p(positions[:, 1]), 'g--', alpha=0.8, linewidth=2,
                           label=f'Trend (r={y_corr:.3f})')
            axes[0,2].legend()
        # Highlight per-row delta spread on Y-axis when range exceeds 50 µm
        y_group_keys = np.round(positions[:, 1], 3)
        unique_y_vals = np.unique(y_group_keys)
        if unique_y_vals.size:
            sorted_y_vals = np.sort(unique_y_vals)
            if unique_y_vals.size > 1:
                typical_gap_y = np.median(np.diff(sorted_y_vals))
            else:
                typical_gap_y = max((y_max - y_min) * 0.05, 1.0)
            y_out_of_range_count = 0
            overall_span_um_y = max((np.nanmax(offsets) - np.nanmin(offsets)) * 1000, 1.0)
            label_pad_um_y = max(5.0, overall_span_um_y * 0.03)
            for idx, y_val in enumerate(sorted_y_vals):
                mask = y_group_keys == y_val
                group_offsets = offsets[mask]
                if group_offsets.size < 2:
                    continue
                group_range_um = (np.nanmax(group_offsets) - np.nanmin(group_offsets)) * 1000
                if group_range_um <= highlight_threshold_um:
                    continue
                y_out_of_range_count += 1
                prev_gap = y_val - sorted_y_vals[idx-1] if idx > 0 else typical_gap_y
                next_gap = sorted_y_vals[idx+1] - y_val if idx < unique_y_vals.size - 1 else typical_gap_y
                left = y_val - prev_gap / 2.0
                right = y_val + next_gap / 2.0
                left = max(left, y_min - y_margin)
                right = min(right, y_max + y_margin)
                group_max_um = np.nanmax(group_offsets) * 1000
                axes[0,2].axvspan(left, right, color='orange', alpha=0.15, zorder=0)
                axes[0,2].text(y_val, group_max_um + label_pad_um_y, f'{group_range_um:.0f}µm',
                               ha='center', va='bottom', fontsize=9, color='darkorange',
                               bbox=dict(facecolor='white', edgecolor='darkorange', boxstyle='round,pad=0.2', alpha=0.85),
                               clip_on=False)
            note_color_y = 'darkorange' if y_out_of_range_count else 'dimgray'
            axes[0,2].annotate(
                f'{y_out_of_range_count}/{unique_y_vals.size} positions > {highlight_threshold_um:.0f} µm',
                xy=(0.0, 1.0), xycoords='axes fraction',
                xytext=(0, 12), textcoords='offset points',
                ha='left', va='bottom', fontsize=9, color=note_color_y,
                bbox=dict(facecolor='white', edgecolor=note_color_y, boxstyle='round,pad=0.2', alpha=0.85))
        axes[0,2].set_xlabel('Y Position (mm)')
        axes[0,2].set_ylabel('Delta (µm)')
        axes[0,2].set_title('Y-Axis Trend Analysis')
        axes[0,2].grid(True, alpha=0.3)

        # 4. Histogram
        axes[1,0].hist(offsets*1000, bins=min(20, len(offsets)//2 + 1), alpha=0.7,
                       edgecolor='black', color='skyblue')
        axes[1,0].axvline(np.mean(offsets)*1000, color='red', linestyle='--', linewidth=2,
                          label=f'Mean: {np.mean(offsets)*1000:.1f}µm')
        axes[1,0].axvline(np.median(offsets)*1000, color='orange', linestyle='--', linewidth=2,
                          label=f'Median: {np.median(offsets)*1000:.1f}µm')
        axes[1,0].set_xlabel('Delta (µm)')
        axes[1,0].set_ylabel('Frequency')
        axes[1,0].set_title('Delta Distribution')
        axes[1,0].legend()
        axes[1,0].grid(True, alpha=0.3)

        # 5. Measurement sequence grouped by rows (intelligent grouping)
        # Use sampling_direction from config to determine grouping and reordering.
        # - 'x': zigzag left-to-right, group by rows, no reordering needed
        # - 'y': zigzag, group by columns, no reordering needed
        # - 'xy': serpentine rows, group by rows, reorder each row left→right
        # - 'yx': serpentine columns, group by columns, reorder each column bottom→top
        
        y_round = np.round(positions[:, 1], 3)
        unique_y, counts_y = np.unique(y_round, return_counts=True)
        x_round = np.round(positions[:, 0], 3)
        unique_x, counts_x = np.unique(x_round, return_counts=True)

        # Determine grouping based on sampling direction
        if self.sampling_direction in ['x', 'xy']:
            # Row-based: group by constant Y
            primary_axis = 'x'
            row_length = int(np.max(counts_y)) if len(unique_y) > 0 else len(offsets)
            needs_reordering = (self.sampling_direction == 'xy')  # Serpentine needs reordering
            group_label = 'Row'
        elif self.sampling_direction in ['y', 'yx']:
            # Column-based: group by constant X
            primary_axis = 'y'
            row_length = int(np.max(counts_x)) if len(unique_x) > 0 else len(offsets)
            needs_reordering = (self.sampling_direction == 'yx')  # Serpentine needs reordering
            group_label = 'Column'
        else:
            # Fallback for unknown direction
            primary_axis = 'x'
            row_length = int(np.max(counts_y)) if len(unique_y) > 0 else len(offsets)
            needs_reordering = False
            group_label = 'Group'

        # Plot chunked groups with distinct colors and boundary markers
        cmap = plt.get_cmap('tab20')
        group_midpoints = []
        x_all = np.arange(len(offsets))
        y_all = offsets * 1000
        
        for g, start in enumerate(range(0, len(offsets), row_length)):
            end = min(start + row_length, len(offsets))
            xs = x_all[start:end]
            
            if needs_reordering:
                # Serpentine: reorder values within each pass to normalize direction
                idx_slice = np.arange(start, end)
                if primary_axis == 'x':
                    order = np.argsort(positions[idx_slice, 0])  # sort by X asc (left→right)
                else:
                    order = np.argsort(positions[idx_slice, 1])  # sort by Y asc (bottom→top)
                ys = y_all[start:end][order]
            else:
                # Zigzag: no reordering, keep acquisition order
                ys = y_all[start:end]
            
            color = cmap(g % 20)
            axes[1,1].plot(xs, ys, '-o', color=color, markersize=4, linewidth=1.6, alpha=0.9)
            if start > 0:
                axes[1,1].axvline(start-0.5, color='gray', linestyle=':', linewidth=1, alpha=0.6)
            group_midpoints.append((start + end - 1) / 2)

        # Global mean and ±1σ band for context
        axes[1,1].axhline(np.mean(offsets)*1000, color='red', linestyle='--', alpha=0.6, linewidth=1.8)
        axes[1,1].fill_between(x_all,
                               (np.mean(offsets) - np.std(offsets))*1000,
                               (np.mean(offsets) + np.std(offsets))*1000,
                               alpha=0.12, color='red')
        # Label groups lightly on the x-axis
        if group_midpoints:
            axes[1,1].set_xticks(group_midpoints)
            axes[1,1].set_xticklabels([f"{group_label} {i+1}" for i in range(len(group_midpoints))], rotation=45, ha='right', fontsize=8)

        axes[1,1].set_xlabel('Measurement Order (grouped by pass)')
        axes[1,1].set_ylabel('Delta (µm)')
        # Set title based on sampling direction
        if self.sampling_direction == 'x':
            axes[1,1].set_title('Measurement Sequence (as is, by row)')
        elif self.sampling_direction == 'y':
            axes[1,1].set_title('Measurement Sequence (as is, by column)')
        elif self.sampling_direction == 'xy':
            axes[1,1].set_title('Measurement Sequence (serpentine, normalized left→right)')
        elif self.sampling_direction == 'yx':
            axes[1,1].set_title('Measurement Sequence (serpentine, normalized bottom→top)')
        else:
            axes[1,1].set_title('Measurement Sequence (grouped by pass)')
        axes[1,1].grid(True, alpha=0.3)

        # 6. Sampling pattern
        axes[1,2].scatter(positions[:, 0], positions[:, 1], c='lightblue', s=60,
                          alpha=0.7, edgecolors='black', linewidth=0.5)
        for i in range(len(positions) - 1):
            axes[1,2].plot([positions[i, 0], positions[i+1, 0]],
                           [positions[i, 1], positions[i+1, 1]], 'gray', alpha=0.6, linewidth=1)
        if len(positions) > 0:
            axes[1,2].scatter(positions[0, 0], positions[0, 1], c='green', s=100, marker='o',
                              edgecolors='darkgreen', linewidth=2, label='Start', zorder=5)
            if len(positions) > 1:
                axes[1,2].scatter(positions[-1, 0], positions[-1, 1], c='red', s=100, marker='s',
                                  edgecolors='darkred', linewidth=2, label='End', zorder=5)
        num_labels = min(5, len(positions))
        for i in range(num_labels):
            axes[1,2].annotate(f'{i+1}', (positions[i, 0], positions[i, 1]),
                               xytext=(5, 5), textcoords='offset points', fontsize=8, color='darkblue')
        if len(positions) > 10:
            for i in range(len(positions) - num_labels, len(positions)):
                axes[1,2].annotate(f'{i+1}', (positions[i, 0], positions[i, 1]),
                                   xytext=(5, 5), textcoords='offset points', fontsize=8, color='darkred')
        axes[1,2].set_xlim(x_min - x_margin, x_max + x_margin)
        axes[1,2].set_ylim(y_min - y_margin, y_max + y_margin)
        axes[1,2].set_xlabel('X Position (mm)')
        axes[1,2].set_ylabel('Y Position (mm)')
        axes[1,2].set_title('Sampling Pattern')
        axes[1,2].grid(True, alpha=0.3)
        axes[1,2].set_aspect('equal', adjustable='box')
        # Add column numbers on top and row numbers on the right for the sampling pattern
        try:
            x_unique_sp = np.unique(np.round(positions[:, 0], 3))
            y_unique_sp = np.unique(np.round(positions[:, 1], 3))

            ax_top_sp = axes[1,2].secondary_xaxis('top')
            ax_top_sp.set_xticks(x_unique_sp)
            ax_top_sp.set_xticklabels([str(i+1) for i in range(len(x_unique_sp))])
            ax_top_sp.set_xlabel('Column', labelpad=8)
            ax_top_sp.tick_params(axis='x', direction='out', pad=4, labelsize=9)

            ax_right_sp = axes[1,2].secondary_yaxis('right')
            ax_right_sp.set_yticks(y_unique_sp)
            ax_right_sp.set_yticklabels([str(i+1) for i in range(len(y_unique_sp))])
            ax_right_sp.set_ylabel('Row', labelpad=8)
            ax_right_sp.tick_params(axis='y', direction='out', pad=4, labelsize=9)
        except Exception:
            pass

        # Move legend to avoid covering the END point: choose opposite corner of end marker
        try:
            if len(positions) > 1:
                x_end, y_end = positions[-1, 0], positions[-1, 1]
            else:
                x_end, y_end = positions[0, 0], positions[0, 1]

            x_mid = (x_min + x_max) / 2.0
            y_mid = (y_min + y_max) / 2.0

            if y_end >= y_mid:  # end in top half
                legend_loc = 'lower right' if x_end < x_mid else 'lower left'
            else:               # end in bottom half
                legend_loc = 'upper right' if x_end < x_mid else 'upper left'

            axes[1,2].legend(loc=legend_loc)
        except Exception:
            # Fallback location
            axes[1,2].legend(loc='upper left')

        fig1.tight_layout(rect=[0, 0, 1, 0.95])
        file_2d = self.output_folder / f"beacon_offset_analysis{file_tag_dbg}_{timestamp}.png"
        fig1.savefig(file_2d, dpi=300, bbox_inches='tight')

        # -------- Page 2: 3D views --------
        fig2 = plt.figure(figsize=(20, 14))
        ax_contact = fig2.add_subplot(2, 2, 1, projection='3d')
        ax_proximity = fig2.add_subplot(2, 2, 2, projection='3d')
        ax_both = fig2.add_subplot(2, 2, 3, projection='3d')
        ax_w = fig2.add_subplot(2, 2, 4, projection='3d')
        fig2.suptitle(f'3D Meshes and Delta Wireframe{title_dbg}', fontsize=16, fontweight='bold', y=0.98)
        try:
            subtitle = self._compose_subtitle()
            if subtitle:
                fig2.text(0.5, 0.965, subtitle, ha='center', va='top', fontsize=10, color='dimgray')
        except Exception:
            pass

        z_contact_um = contact_zs * 1000.0
        z_proximity_um = proximity_zs * 1000.0

        # Contact only
        gx = np.linspace(x_min - x_margin, x_max + x_margin, 90)
        gy = np.linspace(y_min - y_margin, y_max + y_margin, 90)
        GX, GY = np.meshgrid(gx, gy)
        Zc = griddata(positions, z_contact_um, (GX, GY), method='cubic')
        surf_c = ax_contact.plot_surface(GX, GY, Zc, cmap='RdBu_r', linewidth=0.0,
                                         edgecolor='none', antialiased=True, shade=False)
        ax_contact.set_xlabel('X Position (mm)', labelpad=10)
        ax_contact.set_ylabel('Y Position (mm)', labelpad=10)
        ax_contact.set_zlabel('Z (µm)', labelpad=10)
        ax_contact.set_title('Contact Z Mesh')
        ax_contact.view_init(elev=25, azim=45)
        cbar_c = fig2.colorbar(surf_c, ax=ax_contact, shrink=0.7, aspect=16, pad=0.1)
        cbar_c.set_label('Z (µm)', rotation=270, labelpad=15)

        # Proximity only
        gx = np.linspace(x_min - x_margin, x_max + x_margin, 90)
        gy = np.linspace(y_min - y_margin, y_max + y_margin, 90)
        GX, GY = np.meshgrid(gx, gy)
        Zp = griddata(positions, z_proximity_um, (GX, GY), method='cubic')
        surf_p = ax_proximity.plot_surface(GX, GY, Zp, cmap='RdBu_r', linewidth=0.0,
                                           edgecolor='none', antialiased=True, shade=False)
        ax_proximity.set_xlabel('X Position (mm)', labelpad=10)
        ax_proximity.set_ylabel('Y Position (mm)', labelpad=10)
        ax_proximity.set_zlabel('Z (µm)', labelpad=10)
        ax_proximity.set_title('Proximity Z Mesh')
        ax_proximity.view_init(elev=25, azim=45)
        cbar_p = fig2.colorbar(surf_p, ax=ax_proximity, shrink=0.7, aspect=16, pad=0.1)
        cbar_p.set_label('Z (µm)', rotation=270, labelpad=15)

        # Both meshes overlaid (solid colors; no Z offset). Ensure local overlay correctness by masking high/low regions.
        gx = np.linspace(x_min - x_margin, x_max + x_margin, 90)
        gy = np.linspace(y_min - y_margin, y_max + y_margin, 90)
        GX, GY = np.meshgrid(gx, gy)

        Zc = griddata(positions, z_contact_um, (GX, GY), method='cubic')
        Zp = griddata(positions, z_proximity_um, (GX, GY), method='cubic')

        Zc_mask = np.ma.masked_invalid(Zc)
        Zp_mask = np.ma.masked_invalid(Zp)

        # Solid colors (no gradient)
        contact_color = '#2ca02c'   # green
        proximity_color = '#ff7f0e' # orange

        # Split into regions where proximity is higher vs contact is higher
        prox_high = np.greater_equal(Zp_mask, Zc_mask)
        cont_high = np.greater(Zc_mask, Zp_mask)

        Zp_high = np.ma.masked_where(~prox_high, Zp_mask)
        Zp_low  = np.ma.masked_where(prox_high, Zp_mask)
        Zc_high = np.ma.masked_where(~cont_high, Zc_mask)
        Zc_low  = np.ma.masked_where(cont_high, Zc_mask)

        # Draw order: lows first, then highs so the locally higher surface sits on top
        ax_both.plot_surface(GX, GY, Zc_low,
                             color=contact_color, alpha=0.60,
                             linewidth=0.0, edgecolor='none', antialiased=True, shade=False)
        ax_both.plot_surface(GX, GY, Zp_low,
                             color=proximity_color, alpha=0.50,
                             linewidth=0.2, edgecolor='k', antialiased=True, shade=False)
        ax_both.plot_surface(GX, GY, Zc_high,
                             color=contact_color, alpha=0.60,
                             linewidth=0.0, edgecolor='none', antialiased=True, shade=False)
        ax_both.plot_surface(GX, GY, Zp_high,
                             color=proximity_color, alpha=0.50,
                             linewidth=0.2, edgecolor='k', antialiased=True, shade=False)

        ax_both.set_xlabel('X Position (mm)', labelpad=10)
        ax_both.set_ylabel('Y Position (mm)', labelpad=10)
        ax_both.set_zlabel('Z (µm)', labelpad=10)
        ax_both.set_title('Overlay: Contact vs Proximity')
        ax_both.view_init(elev=25, azim=45)
        legend_handles = [
            Patch(facecolor='#2ca02c', edgecolor='none', label='Contact'),
            Patch(facecolor='#ff7f0e', edgecolor='k', label='Proximity')
        ]
        ax_both.legend(handles=legend_handles, loc='upper left')

        # Delta 3D wireframe (triangulated) with larger points
        zvals_um = offsets * 1000.0
        tri = Triangulation(positions[:, 0], positions[:, 1])
        # Build a unique set of triangle edges
        edges = set()
        for a, b, c in tri.triangles:
            edges.add((min(a, b), max(a, b)))
            edges.add((min(b, c), max(b, c)))
            edges.add((min(c, a), max(c, a)))
        # Assemble 3D line segments for the wireframe
        segs = []
        X = positions[:, 0]
        Y = positions[:, 1]
        Z = zvals_um
        for i, j in edges:
            segs.append([(X[i], Y[i], Z[i]), (X[j], Y[j], Z[j])])
        lc = Line3DCollection(segs, colors='gray', linewidths=0.6)
        ax_w.add_collection3d(lc)
        # Subplot visual separation: subtle background for delta view
        # ax_w.set_facecolor('#fff7ed')  # light warm background to distinguish from mesh plots
        sc = ax_w.scatter(X, Y, Z, c=Z, cmap='RdBu_r', s=48, alpha=0.95, depthshade=True)
        ax_w.set_xlabel('X Position (mm)', labelpad=10)
        ax_w.set_ylabel('Y Position (mm)', labelpad=10)
        ax_w.set_zlabel('Delta (µm)', labelpad=10)
        ax_w.set_title('DELTA OFFSET VIEW — Wireframe')
        # Badge annotation inside axes for clarity
        ax_w.text2D(
            0.02, 0.98, 'Note that this is not a mesh', transform=ax_w.transAxes,
            ha='left', va='top', fontsize=11, color='#9a3412',
            bbox=dict(facecolor='#fff7ed', edgecolor='#d97706', boxstyle='round,pad=0.3', alpha=0.95)
        )
        ax_w.view_init(elev=25, azim=45)
        cbar_w = fig2.colorbar(sc, ax=ax_w, shrink=0.8, aspect=18, pad=0.1)
        cbar_w.set_label('Delta (µm)', rotation=270, labelpad=15)

        # Final layout and save single 3D page
        fig2.tight_layout(rect=[0, 0, 1, 0.95])
        # After layout, draw a highlighted border around the delta subplot to separate it visually
        # try:
        #     pos = ax_w.get_position()
        #     border = matplotlib.patches.FancyBboxPatch(
        #         (pos.x0 - 0.006, pos.y0 - 0.006),
        #         pos.width + 0.012, pos.height + 0.012,
        #         boxstyle='round,pad=0.003', transform=fig2.transFigure,
        #         facecolor='none', edgecolor='#d97706', linewidth=2.2
        #     )
        #     fig2.add_artist(border)
        # except Exception:
        #     pass
        file_3d = self.output_folder / f"beacon_offset_analysis_3D_meshes&offset{file_tag_dbg}_{timestamp}.png"
        fig2.savefig(file_3d, dpi=300, bbox_inches='tight')

        # -------- Page 3: Overlay multi-view comparisons --------
        # Show inclined (elev=20) and flat (elev=0) front views on the same page.
        fig3 = plt.figure(figsize=(20, 14))
        ax_x_incl = fig3.add_subplot(2, 2, 1, projection='3d')
        ax_x_flat = fig3.add_subplot(2, 2, 2, projection='3d')
        ax_y_incl = fig3.add_subplot(2, 2, 3, projection='3d')
        ax_y_flat = fig3.add_subplot(2, 2, 4, projection='3d')
        fig3.suptitle(f'Overlay: Contact vs Proximity — Front Views (X and Y) {title_dbg}', fontsize=16, fontweight='bold', y=0.98)
        try:
            subtitle = self._compose_subtitle()
            if subtitle:
                fig3.text(0.5, 0.965, subtitle, ha='center', va='top', fontsize=10, color='dimgray')
        except Exception:
            pass

        def apply_view(ax, elev, azim, title):
            ax.set_title(title)
            ax.view_init(elev=elev, azim=azim)

        def plot_overlay(ax):
            gx = np.linspace(x_min - x_margin, x_max + x_margin, 90)
            gy = np.linspace(y_min - y_margin, y_max + y_margin, 90)
            GX, GY = np.meshgrid(gx, gy)
            Zc = griddata(positions, z_contact_um, (GX, GY), method='cubic')
            Zp = griddata(positions, z_proximity_um, (GX, GY), method='cubic')
            Zc_mask = np.ma.masked_invalid(Zc)
            Zp_mask = np.ma.masked_invalid(Zp)
            contact_color = '#2ca02c'
            proximity_color = '#ff7f0e'

            prox_high = np.greater_equal(Zp_mask, Zc_mask)
            cont_high = np.greater(Zc_mask, Zp_mask)

            Zp_high = np.ma.masked_where(~prox_high, Zp_mask)
            Zp_low  = np.ma.masked_where(prox_high, Zp_mask)
            Zc_high = np.ma.masked_where(~cont_high, Zc_mask)
            Zc_low  = np.ma.masked_where(cont_high, Zc_mask)

            ax.plot_surface(GX, GY, Zc_low, color=contact_color, alpha=0.60,
                            linewidth=0.0, edgecolor='none', antialiased=True, shade=False)
            ax.plot_surface(GX, GY, Zp_low, color=proximity_color, alpha=0.50,
                            linewidth=0.2, edgecolor='k', antialiased=True, shade=False)
            ax.plot_surface(GX, GY, Zc_high, color=contact_color, alpha=0.60,
                            linewidth=0.0, edgecolor='none', antialiased=True, shade=False)
            ax.plot_surface(GX, GY, Zp_high, color=proximity_color, alpha=0.50,
                            linewidth=0.2, edgecolor='k', antialiased=True, shade=False)
            ax.set_xlabel('X Position (mm)', labelpad=10)
            ax.set_ylabel('Y Position (mm)', labelpad=10)
            ax.set_zlabel('Z (µm)', labelpad=10)

        # Plot overlays for Side X: inclined and flat
        plot_overlay(ax_x_incl)
        apply_view(ax_x_incl, elev=20, azim=0, title='Side X — front (elev=20, azim=0)')

        plot_overlay(ax_x_flat)
        apply_view(ax_x_flat, elev=0, azim=0, title='Side X — front (elev=0, azim=0)')
        # Declutter: remove X ticks on Side X front
        ax_x_flat.set_xticks([])

        # Plot overlays for Side Y: inclined and flat
        plot_overlay(ax_y_incl)
        apply_view(ax_y_incl, elev=20, azim=90, title='Side Y — front (elev=20, azim=90)')

        plot_overlay(ax_y_flat)
        apply_view(ax_y_flat, elev=0, azim=90, title='Side Y — front (elev=0, azim=90)')
        # Declutter: remove Y ticks on Side Y front
        ax_y_flat.set_yticks([])

        # Add legend to the first subplot only to avoid clutter
        legend_handles_mv = [
            Patch(facecolor='#2ca02c', edgecolor='none', label='Contact'),
            Patch(facecolor='#ff7f0e', edgecolor='k', label='Proximity')
        ]
        ax_x_incl.legend(handles=legend_handles_mv, loc='upper left')

        # Final layout and save multi-view overlay page
        fig3.tight_layout(rect=[0, 0, 1, 0.95])
        file_3d_views = self.output_folder / f"beacon_offset_analysis_3D_meshes_overlay{file_tag_dbg}_{timestamp}.png"
        fig3.savefig(file_3d_views, dpi=300, bbox_inches='tight')

        # Cleanup to free memory once generation completes
        try:
            # Close only the figures we created to avoid side effects
            plt.close(fig1)
            plt.close(fig2)
            plt.close(fig3)
        except Exception:
            pass
        # Drop local arrays explicitly and force GC
        try:
            del positions, offsets, contact_zs, proximity_zs
        except Exception:
            pass
        gc.collect()

        return str(file_3d_views)
